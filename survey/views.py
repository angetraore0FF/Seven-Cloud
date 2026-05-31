from urllib.parse import urlencode
from datetime import timedelta
import logging
import random
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.http import JsonResponse, HttpResponse, FileResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_http_methods
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.utils import timezone
from .services.fichiers import upload_fichier, delete_fichier_storage
from .utils.pme import get_or_create_pme
from .services.nextcloud import NextcloudStorage, NextcloudError, slug_pme
from .services.quota import stats_stockage_utilisateur, stats_stockage_pme
from .models import (
    Fichier, Dossier, UserProfile, PME, Reponse,
    DemandeMotDePasse, DemandeAugmentationQuota, EmailOTP,
)

logger = logging.getLogger(__name__)
from .forms import (
    UploadFichierForm, CreerDossierForm, UserProfileForm,
    PMEForm, AdminUserForm, AdminAccountForm, AdminPasswordForm,
    ProfilePhotoForm, UserAccountForm, UserProfileSettingsForm,
    DemandeMotDePasseForm, DemandeAugmentationQuotaForm,
)


def is_admin(user):
    return user.is_authenticated and (user.is_superuser or
                                     (hasattr(user, 'profile') and user.profile.role == 'admin'))


def _pme_readiness(pme):
    if pme.a_repondu:
        return 100
    reponses_count = pme.reponses.count()
    if reponses_count > 0:
        return min(90, 30 + reponses_count * 10)
    return 25


def _admin_context(request, active_nav):
    profile = getattr(request.user, 'profile', None)
    if request.user.is_authenticated and profile is None:
        profile, _ = UserProfile.objects.get_or_create(
            user=request.user,
            defaults={'role': 'admin'},
        )
    return {
        'user': request.user,
        'profile': profile,
        'active_nav': active_nav,
    }


def _redirect_utilisateurs(pme_filter=''):
    url = reverse('admin_utilisateurs')
    if pme_filter:
        url += '?' + urlencode({'pme': pme_filter})
    return redirect(url)


def _ensure_user_profile(user):
    UserProfile.objects.get_or_create(user=user)
    return UserProfile.objects.select_related('pme').get(user=user)


def _user_context(request, active_nav):
    profile = _ensure_user_profile(request.user)
    is_local_network = request.META.get('REMOTE_ADDR') in ['127.0.0.1', '::1', 'localhost']
    pme = profile.pme
    storage = stats_stockage_utilisateur(profile)
    ctx = {
        'user': request.user,
        'profile': profile,
        'pme': pme,
        'pme_nom': pme.nom if pme else None,
        'pme_slug': slug_pme(pme.nom) if pme else '',
        'active_nav': active_nav,
        'is_local_network': is_local_network,
        'used_storage_gb': profile.espace_utilise(),
        'quota_gb': profile.quota_stockage,
        'pourcentage_utilise': storage['pourcentage_utilise'],
        'used_storage_label': storage['used_label'],
        'reste_label': storage['reste_label'],
    }
    if pme:
        ctx.update({
            'pme_used_label': storage['pme_used_label'],
            'pme_quota_gb': storage['pme_quota_gb'],
            'pme_pourcentage': storage['pme_pourcentage'],
            'pme_reste_label': storage['pme_reste_label'],
        })
    return ctx


def _redirect_fichiers(vue='tous'):
    url = reverse('user_fichiers')
    if vue and vue != 'tous':
        url += '?' + urlencode({'vue': vue})
    return redirect(url)


def _get_user_file(request, user, file_id):
    return get_object_or_404(Fichier, pk=file_id, proprietaire=user)


def _user_can_access_fichier(user, fichier):
    if fichier.proprietaire_id == user.id:
        return True
    return fichier.partage_avec.filter(pk=user.pk).exists()


def _handle_fichier_post(request, user):
    """Traite les actions POST sur les fichiers. Retourne une redirect response ou None."""
    vue = request.POST.get('vue', 'tous')

    if request.POST.get('upload_file') and request.FILES.get('file'):
        profile = _ensure_user_profile(user)
        try:
            upload_fichier(user, request.FILES['file'], profile)
            label = 'sur Nextcloud' if settings.NEXTCLOUD_ENABLED else 'localement'
            messages.success(request, f'Fichier téléversé et chiffré ({label}).')
        except ValueError as exc:
            messages.error(request, str(exc))
        return _redirect_fichiers(vue)

    file_id = request.POST.get('file_id')
    if not file_id:
        return None

    fichier = _get_user_file(request, user, file_id)

    if 'delete_file' in request.POST:
        fichier.est_corbeille = True
        fichier.save()
        messages.success(request, 'Fichier déplacé vers la corbeille.')
        return _redirect_fichiers(vue)

    if 'restore_file' in request.POST:
        fichier.est_corbeille = False
        fichier.save()
        messages.success(request, 'Fichier restauré.')
        return _redirect_fichiers('corbeille')

    if 'delete_permanent' in request.POST:
        delete_fichier_storage(fichier)
        fichier.delete()
        messages.success(request, 'Fichier supprimé définitivement.')
        return _redirect_fichiers('corbeille')

    if 'toggle_favori' in request.POST:
        fichier.est_favori = not fichier.est_favori
        fichier.save()
        messages.success(request, 'Favori mis à jour.')
        return _redirect_fichiers(vue)

    if 'share_file' in request.POST:
        username = request.POST.get('share_username', '').strip()
        target = User.objects.filter(username=username).exclude(pk=user.pk).first()
        if target:
            fichier.partage_avec.add(target)
            messages.success(request, f'Fichier partagé avec {username}.')
            return redirect('user_partages')
        messages.error(request, 'Utilisateur introuvable.')
        return _redirect_fichiers(vue)

    return None


def _handle_partage_post(request, user):
    file_id = request.POST.get('file_id')
    if not file_id or 'unshare_file' not in request.POST:
        return None

    fichier = get_object_or_404(Fichier, pk=file_id, proprietaire=user)
    username = request.POST.get('unshare_username', '').strip()
    target = User.objects.filter(username=username).first()
    if target:
        fichier.partage_avec.remove(target)
        messages.success(request, f'Partage retiré pour {username}.')
    else:
        messages.error(request, 'Utilisateur introuvable.')
    return redirect('user_partages')


@never_cache
@login_required
def dashboard_user(request):
    """Vue du dashboard utilisateur — aperçu"""
    user = request.user
    profile = _ensure_user_profile(user)

    fichiers = Fichier.objects.filter(proprietaire=user, est_corbeille=False)
    fichiers_recents = fichiers.order_by('-date_upload')[:10]
    fichiers_partages = Fichier.objects.filter(partage_avec=user, est_corbeille=False)
    fichiers_partages_envoyes = (
        Fichier.objects.filter(proprietaire=user, est_corbeille=False)
        .annotate(nb_partages=Count('partage_avec', distinct=True))
        .filter(nb_partages__gt=0)
        .count()
    )

    context = {
        **_user_context(request, 'dashboard'),
        'fichiers': fichiers_recents,
        'total_fichiers': fichiers.count(),
        'total_chiffres': fichiers.filter(chiffre=True).count(),
        'total_partages': fichiers_partages.count(),
        'total_partages_envoyes': fichiers_partages_envoyes,
        'fichiers_favoris': fichiers.filter(est_favori=True).count(),
    }
    return render(request, 'user/dashboard.html', context)


@never_cache
@login_required
def user_fichiers(request):
    """Liste et gestion des fichiers de l'utilisateur"""
    user = request.user
    _ensure_user_profile(user)

    vue = request.GET.get('vue', 'tous')
    if vue not in ('tous', 'favoris', 'corbeille'):
        vue = 'tous'

    if request.method == 'POST':
        response = _handle_fichier_post(request, user)
        if response:
            return response

    qs = Fichier.objects.filter(proprietaire=user)
    if vue == 'corbeille':
        qs = qs.filter(est_corbeille=True)
    else:
        qs = qs.filter(est_corbeille=False)
        if vue == 'favoris':
            qs = qs.filter(est_favori=True)

    fichiers = qs.order_by('-date_upload')

    profile = _ensure_user_profile(request.user)
    collegues = []
    if profile.pme_id:
        collegues = User.objects.filter(profile__pme=profile.pme).exclude(
            pk=request.user.pk
        ).order_by('username')

    context = {
        **_user_context(request, 'fichiers'),
        'fichiers': fichiers,
        'vue': vue,
        'nextcloud_enabled': settings.NEXTCLOUD_ENABLED,
        'collegues': collegues,
    }
    return render(request, 'user/fichiers.html', context)


@never_cache
@login_required
@require_http_methods(["POST"])
def api_upload_fichier(request):
    """Upload JSON avec progression côté client (XHR)."""
    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'Aucun fichier'}, status=400)
    profile = _ensure_user_profile(request.user)
    try:
        fichier = upload_fichier(request.user, uploaded, profile)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=502)
    except Exception as exc:
        return JsonResponse({'error': f"Erreur serveur : {exc}"}, status=500)
    storage = stats_stockage_utilisateur(profile)
    payload = {
        'id': fichier.pk,
        'nom': fichier.nom,
        'taille': fichier.taille_formatee(),
        'nextcloud': fichier.stockage_externe,
        'storage': {
            'used_label': storage['used_label'],
            'quota_gb': storage['quota_gb'],
            'pourcentage': storage['pourcentage_utilise'],
            'reste_label': storage['reste_label'],
        },
    }
    if profile.pme:
        payload['storage']['pme_used_label'] = storage['pme_used_label']
        payload['storage']['pme_quota_gb'] = storage['pme_quota_gb']
        payload['storage']['pme_pourcentage'] = storage['pme_pourcentage']
    return JsonResponse(payload)


@never_cache
@login_required
def telecharger_fichier(request, pk):
    """Téléchargement sécurisé (local ou Nextcloud)."""
    fichier = get_object_or_404(Fichier, pk=pk)
    if not _user_can_access_fichier(request.user, fichier):
        raise PermissionDenied

    if fichier.est_sur_nextcloud and settings.NEXTCLOUD_ENABLED:
        try:
            content = NextcloudStorage().download(fichier.nextcloud_path)
            response = HttpResponse(
                content,
                content_type=fichier.type_mime or 'application/octet-stream',
            )
            response['Content-Disposition'] = f'attachment; filename="{fichier.nom}"'
            return response
        except NextcloudError:
            messages.error(request, 'Impossible de récupérer le fichier sur Nextcloud.')
            return redirect('user_fichiers')

    if fichier.fichier:
        return FileResponse(
            fichier.fichier.open('rb'),
            as_attachment=True,
            filename=fichier.nom,
        )

    messages.error(request, 'Fichier introuvable.')
    return redirect('user_fichiers')


@never_cache
@login_required
def user_partages(request):
    """Fichiers partagés reçus et envoyés"""
    user = request.user
    _ensure_user_profile(user)

    if request.method == 'POST':
        response = _handle_partage_post(request, user)
        if response:
            return response

    fichiers_recus = (
        Fichier.objects.filter(partage_avec=user, est_corbeille=False)
        .select_related('proprietaire')
        .order_by('-date_upload')
    )
    fichiers_envoyes = (
        Fichier.objects.filter(proprietaire=user, est_corbeille=False)
        .annotate(nb_partages=Count('partage_avec', distinct=True))
        .filter(nb_partages__gt=0)
        .prefetch_related('partage_avec')
        .order_by('-date_upload')
    )
    collegues = []
    profile = _ensure_user_profile(user)
    if profile.pme_id:
        collegues = User.objects.filter(
            profile__pme=profile.pme,
        ).exclude(pk=user.pk).order_by('username')

    context = {
        **_user_context(request, 'partages'),
        'fichiers_recus': fichiers_recus,
        'fichiers_envoyes': fichiers_envoyes,
        'total_partages_envoyes': fichiers_envoyes.count(),
        'collegues': collegues,
    }
    return render(request, 'user/partages.html', context)


@never_cache
@login_required
def user_parametres(request):
    """Paramètres du compte utilisateur"""
    user = request.user
    profile = _ensure_user_profile(user)

    account_form = UserAccountForm(instance=user)
    photo_form = ProfilePhotoForm(instance=profile)
    profile_form = UserProfileSettingsForm(instance=profile)
    demande_form = DemandeMotDePasseForm()
    demande_en_attente = DemandeMotDePasse.objects.filter(user=user, traite=False).first()
    demande_quota_form = DemandeAugmentationQuotaForm(quota_actuel=profile.quota_stockage)
    demande_quota_en_attente = DemandeAugmentationQuota.objects.filter(user=user, traite=False).first()

    if request.method == 'POST':
        if 'upload_photo' in request.POST:
            photo_form = ProfilePhotoForm(request.POST, request.FILES, instance=profile)
            if photo_form.is_valid():
                photo_form.save()
                messages.success(request, 'Photo de profil mise à jour.')
                return redirect('user_parametres')
            messages.error(request, 'Impossible de mettre à jour la photo.')

        elif 'update_account' in request.POST:
            account_form = UserAccountForm(request.POST, instance=user)
            if account_form.is_valid():
                account_form.save()
                messages.success(request, 'Compte mis à jour.')
                return redirect('user_parametres')

        elif 'update_profile' in request.POST:
            profile_form = UserProfileSettingsForm(request.POST, instance=profile)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Profil mis à jour.')
                return redirect('user_parametres')

        elif 'demande_mdp' in request.POST:
            if demande_en_attente:
                messages.warning(request, 'Une demande est déjà en attente de traitement par l\'administrateur.')
            else:
                demande_form = DemandeMotDePasseForm(request.POST)
                if demande_form.is_valid():
                    demande = demande_form.save(commit=False)
                    demande.user = user
                    demande.save()
                    messages.success(request, 'Demande envoyée. Un administrateur vous contactera après traitement.')
                    return redirect('user_parametres')

        elif 'demande_quota' in request.POST:
            if demande_quota_en_attente:
                messages.warning(request, 'Une demande de quota est déjà en attente.')
            else:
                demande_quota_form = DemandeAugmentationQuotaForm(
                    request.POST, quota_actuel=profile.quota_stockage
                )
                if demande_quota_form.is_valid():
                    demande = demande_quota_form.save(commit=False)
                    demande.user = user
                    demande.quota_actuel = profile.quota_stockage
                    demande.save()
                    messages.success(request, 'Demande d\'augmentation de quota envoyée.')
                    return redirect('user_parametres')

    context = {
        **_user_context(request, 'parametres'),
        'account_form': account_form,
        'photo_form': photo_form,
        'profile_form': profile_form,
        'demande_form': demande_form,
        'demande_en_attente': demande_en_attente,
        'demande_quota_form': demande_quota_form,
        'demande_quota_en_attente': demande_quota_en_attente,
    }
    return render(request, 'user/parametres.html', context)


@never_cache
@login_required
@user_passes_test(is_admin)
def dashboard_admin(request):
    """Vue du dashboard administrateur"""
    total_users = User.objects.filter(is_active=True).count()
    total_pme = PME.objects.count()
    pme_repondu = PME.objects.filter(a_repondu=True).count()

    volume_total = Fichier.objects.aggregate(total=Sum('taille'))['total'] or 0
    volume_total_go = round(volume_total / (1024**3), 2)

    pmes = []
    for pme in PME.objects.all().order_by('-date_creation'):
        pmes.append({
            'name': pme.nom,
            'secteur': pme.secteur,
            'readiness_score': _pme_readiness(pme),
        })

    context = {
        **_admin_context(request, 'apercu'),
        'total_users': total_users,
        'total_pme': total_pme,
        'total_pmes': total_pme,
        'pme_repondu': pme_repondu,
        'pme_non_repondu': total_pme - pme_repondu,
        'volume_total': volume_total_go,
        'total_volume_gb': volume_total_go,
        'pmes': pmes,
        'logs': [],
    }

    return render(request, 'admin/dashboard.html', context)


@never_cache
@login_required
@user_passes_test(is_admin)
def admin_pme(request):
    """Gestion des PME inscrites sur la plateforme"""
    edit_id = request.GET.get('edit')
    pme_edit = None
    form = PMEForm()

    if edit_id:
        pme_edit = get_object_or_404(PME, pk=edit_id)
        form = PMEForm(instance=pme_edit)

    if request.method == 'POST':
        if 'delete_pme' in request.POST:
            pme = get_object_or_404(PME, pk=request.POST.get('pme_id'))
            nom = pme.nom
            pme.delete()
            messages.success(request, f'La PME « {nom} » a été supprimée.')
            return redirect('admin_pme')

        pme_id = request.POST.get('pme_id')
        if pme_id:
            pme_edit = get_object_or_404(PME, pk=pme_id)
            form = PMEForm(request.POST, request.FILES, instance=pme_edit)
        else:
            form = PMEForm(request.POST, request.FILES)

        if form.is_valid():
            form.save()
            messages.success(request, 'PME enregistrée avec succès.')
            return redirect('admin_pme')

    pmes = PME.objects.annotate(nb_utilisateurs=Count('utilisateurs')).order_by('-date_creation')
    pmes_stats = []
    for pme in pmes:
        st = stats_stockage_pme(pme)
        pmes_stats.append({
            'pme': pme,
            **st,
        })

    context = {
        **_admin_context(request, 'pme'),
        'pmes': pmes,
        'pmes_stats': pmes_stats,
        'form': form,
        'pme_edit': pme_edit,
    }
    return render(request, 'admin/pme.html', context)


@never_cache
@login_required
@user_passes_test(is_admin)
def admin_utilisateurs(request):
    """Gestion des utilisateurs par PME"""
    edit_id = request.GET.get('edit')
    user_edit = None
    form = AdminUserForm()

    if edit_id:
        user_edit = get_object_or_404(User, pk=edit_id)
        form = AdminUserForm(instance=user_edit)

    if request.method == 'POST':
        if 'delete_user' in request.POST:
            target = get_object_or_404(User, pk=request.POST.get('user_id'))
            if target == request.user:
                messages.error(request, 'Vous ne pouvez pas supprimer votre propre compte.')
            else:
                username = target.username
                target.delete()
                messages.success(request, f'L\'utilisateur « {username} » a été supprimé.')
            return redirect('admin_utilisateurs')

        if 'toggle_active' in request.POST:
            target = get_object_or_404(User, pk=request.POST.get('user_id'))
            if target == request.user:
                messages.error(request, 'Vous ne pouvez pas désactiver votre propre compte.')
            else:
                target.is_active = not target.is_active
                target.save()
                statut = 'activé' if target.is_active else 'désactivé'
                messages.success(request, f'Utilisateur {statut}.')
            return _redirect_utilisateurs(request.POST.get('pme_filter', ''))

        if 'traiter_demande_mdp' in request.POST:
            demande = get_object_or_404(DemandeMotDePasse, pk=request.POST.get('demande_id'), traite=False)
            nouveau_mdp = request.POST.get('nouveau_mot_de_passe', '').strip()
            if nouveau_mdp:
                demande.user.set_password(nouveau_mdp)
                demande.user.save()
            demande.traite = True
            demande.date_traitement = timezone.now()
            demande.save()
            messages.success(request, f'Demande traitée pour {demande.user.username}.')
            return redirect('admin_utilisateurs')

        if 'traiter_demande_quota' in request.POST:
            demande = get_object_or_404(DemandeAugmentationQuota, pk=request.POST.get('demande_id'), traite=False)
            try:
                quota_accorde = int(request.POST.get('quota_accorde', demande.quota_demande))
            except (TypeError, ValueError):
                quota_accorde = demande.quota_demande
            if quota_accorde < demande.quota_actuel:
                messages.error(request, 'Le quota accordé doit être supérieur au quota actuel.')
                return redirect('admin_utilisateurs')
            profile_target = _ensure_user_profile(demande.user)
            profile_target.quota_stockage = quota_accorde
            profile_target.save()
            demande.quota_accorde = quota_accorde
            demande.traite = True
            demande.date_traitement = timezone.now()
            demande.save()
            messages.success(request, f'Quota de {demande.user.username} porté à {quota_accorde} Go.')
            return redirect('admin_utilisateurs')

        user_id = request.POST.get('user_id')
        if user_id:
            user_edit = get_object_or_404(User, pk=user_id)
            form = AdminUserForm(request.POST, request.FILES, instance=user_edit)
        else:
            form = AdminUserForm(request.POST, request.FILES)

        if form.is_valid():
            form.save()
            messages.success(request, 'Utilisateur enregistré avec succès.')
            return _redirect_utilisateurs(request.POST.get('pme_filter', ''))

    pme_filter = request.GET.get('pme', '')
    role_filter = request.GET.get('role', '')

    users = User.objects.select_related('profile', 'profile__pme').exclude(
        Q(is_superuser=True) | Q(profile__role='admin')
    )

    if pme_filter:
        users = users.filter(profile__pme_id=pme_filter)
    if role_filter:
        users = users.filter(profile__role=role_filter)

    users = users.order_by('username')
    all_pmes = PME.objects.all().order_by('nom')

    demandes_mdp = DemandeMotDePasse.objects.filter(traite=False).select_related('user', 'user__profile', 'user__profile__pme')
    demandes_quota = DemandeAugmentationQuota.objects.filter(traite=False).select_related('user', 'user__profile', 'user__profile__pme')

    context = {
        **_admin_context(request, 'utilisateurs'),
        'users': users,
        'all_pmes': all_pmes,
        'form': form,
        'user_edit': user_edit,
        'pme_filter': pme_filter,
        'role_filter': role_filter,
        'total_users': users.count(),
        'demandes_mdp': demandes_mdp,
        'demandes_quota': demandes_quota,
    }
    return render(request, 'admin/utilisateurs.html', context)


@never_cache
@login_required
@user_passes_test(is_admin)
def admin_securite(request):
    """Normes de sécurité appliquées aux fichiers"""
    normes = [
        {
            'titre': 'Chiffrement au repos',
            'norme': 'AES-256',
            'description': 'Tous les fichiers stockés sur la plateforme sont chiffrés avec l\'algorithme AES-256 en mode GCM.',
            'icone': 'lock',
        },
        {
            'titre': 'Chiffrement en transit',
            'norme': 'TLS 1.3',
            'description': 'Les échanges entre le client et le serveur sont protégés par TLS 1.3 avec des certificats à jour.',
            'icone': 'shield',
        },
        {
            'titre': 'Intégrité des fichiers',
            'norme': 'SHA-256',
            'description': 'Une empreinte SHA-256 est calculée à l\'upload pour détecter toute altération ultérieure.',
            'icone': 'check',
        },
        {
            'titre': 'Gestion des clés',
            'norme': 'PBKDF2 / HKDF',
            'description': 'Les clés de chiffrement sont dérivées via PBKDF2 et renouvelées périodiquement selon HKDF.',
            'icone': 'key',
        },
        {
            'titre': 'Contrôle d\'accès',
            'norme': 'RBAC',
            'description': 'Accès basé sur les rôles (admin, employé, RH, compta, invité) avec isolation par PME.',
            'icone': 'users',
        },
        {
            'titre': 'Conformité',
            'norme': 'RGPD / ISO 27001',
            'description': 'Traitement des données conforme au RGPD, aligné sur les bonnes pratiques ISO 27001.',
            'icone': 'document',
        },
    ]

    context = {
        **_admin_context(request, 'securite'),
        'normes': normes,
    }
    return render(request, 'admin/securite.html', context)


@never_cache
@login_required
@user_passes_test(is_admin)
def admin_parametres(request):
    """Gestion du compte administrateur connecté"""
    profile = getattr(request.user, 'profile', None)
    if not profile:
        profile = UserProfile.objects.create(user=request.user, role='admin')

    account_form = AdminAccountForm(instance=request.user)
    password_form = AdminPasswordForm(user=request.user)
    photo_form = ProfilePhotoForm(instance=profile)

    if request.method == 'POST':
        if 'upload_photo' in request.POST:
            photo_form = ProfilePhotoForm(request.POST, request.FILES, instance=profile)
            if photo_form.is_valid():
                photo_form.save()
                messages.success(request, 'Photo de profil mise à jour.')
                return redirect('admin_parametres')
            messages.error(request, 'Impossible de mettre à jour la photo.')

        elif 'update_account' in request.POST:
            account_form = AdminAccountForm(request.POST, instance=request.user)
            if account_form.is_valid():
                account_form.save()
                messages.success(request, 'Profil mis à jour.')
                return redirect('admin_parametres')

        elif 'change_password' in request.POST:
            password_form = AdminPasswordForm(request.user, request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, password_form.user)
                messages.success(request, 'Mot de passe modifié.')
                return redirect('admin_parametres')

    context = {
        **_admin_context(request, 'parametres'),
        'account_form': account_form,
        'password_form': password_form,
        'photo_form': photo_form,
        'profile': profile,
    }
    return render(request, 'admin/parametres.html', context)


@never_cache
def router_dashboard(request):
    """Routeur qui redirige vers le bon dashboard selon les permissions"""
    if not request.user.is_authenticated:
        return redirect('login')

    if is_admin(request.user):
        return redirect('dashboard_admin')
    else:
        return redirect('dashboard_user')


@never_cache
def custom_login(request):
    """Vue personnalisée de connexion"""
    if request.user.is_authenticated:
        if is_admin(request.user):
            return redirect('dashboard_admin')
        else:
            return redirect('dashboard_user')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            otp, otp_err = _create_and_send_otp(
                user=user,
                purpose=EmailOTP.PURPOSE_LOGIN,
                email=request.POST.get('email') or user.email,
            )
            if not otp:
                if settings.DEBUG and otp_err:
                    messages.error(request, f"Details SMTP: {otp_err}")
                return render(request, 'registration/login.html', {
                    'form': {'errors': True},
                    'next': request.GET.get('next', '')
                })

            request.session['pending_login_user_id'] = user.id
            request.session['pending_next_url'] = request.POST.get('next', '')
            messages.info(request, 'Un code OTP a ete envoye par email pour valider votre connexion.')
            return redirect('verify_login_otp')
        else:
            return render(request, 'registration/login.html', {
                'form': {'errors': True},
                'next': request.GET.get('next', '')
            })

    return render(request, 'registration/login.html', {
        'next': request.GET.get('next', '')
    })


def custom_register(request):
    """Vue personnalisée d'inscription"""
    if request.user.is_authenticated:
        return redirect('router_dashboard')

    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            email = request.POST.get('email', '').strip()
            if not email:
                form.add_error(None, "L'adresse email est obligatoire pour recevoir le code OTP.")
            elif User.objects.filter(email__iexact=email).exists():
                form.add_error(None, "Cette adresse email est deja utilisee.")
            else:
                with transaction.atomic():
                    user = form.save(commit=False)
                    user.email = email
                    user.is_active = False
                    user.save()

                    company_name = request.POST.get('company_name')
                    sector = request.POST.get('sector')
                    phone = request.POST.get('phone')

                    pme = None
                    if company_name:
                        pme = get_or_create_pme(
                            company_name,
                            secteur=sector or 'autre',
                            email_contact=email or f'{user.username}@seven.ai',
                            telephone=phone or '',
                        )

                    UserProfile.objects.create(
                        user=user,
                        role='employe',
                        pme=pme,
                        quota_stockage=50,
                    )

                otp, otp_err = _create_and_send_otp(
                    user=user,
                    purpose=EmailOTP.PURPOSE_SIGNUP,
                    email=email,
                )
                if not otp:
                    user.delete()
                    form.add_error(None, "Impossible d'envoyer l'OTP par email. Verifiez la configuration email.")
                    if settings.DEBUG and otp_err:
                        form.add_error(None, f"Details SMTP: {otp_err}")
                else:
                    request.session['pending_signup_user_id'] = user.id
                    messages.success(request, "Inscription creee. Entrez le code OTP recu par email pour activer votre compte.")
                    return redirect('verify_signup_otp')
        return render(request, 'registration/register.html', {
            'form': form,
            'company_name': request.POST.get('company_name', ''),
            'sector': request.POST.get('sector', ''),
            'company_size': request.POST.get('company_size', ''),
            'phone': request.POST.get('phone', ''),
        })

    return render(request, 'registration/register.html', {
        'form': UserCreationForm()
    })


def _generate_otp_code():
    return f"{random.randint(0, 999999):06d}"


def _create_and_send_otp(user, purpose, email):
    if not email:
        return None, "Adresse email manquante"

    EmailOTP.objects.filter(
        user=user,
        purpose=purpose,
        consumed_at__isnull=True,
    ).delete()

    raw_code = _generate_otp_code()
    otp = EmailOTP.objects.create(
        user=user,
        purpose=purpose,
        code_hash=make_password(raw_code),
        expires_at=timezone.now() + timedelta(minutes=10),
    )

    try:
        action = "inscription" if purpose == EmailOTP.PURPOSE_SIGNUP else "connexion"
        send_mail(
            subject=f"Votre code OTP Seven.AI ({action})",
            message=(
                f"Bonjour {user.username},\n\n"
                f"Votre code OTP est : {raw_code}\n"
                "Ce code expire dans 10 minutes.\n\n"
                "Si vous n'etes pas a l'origine de cette demande, ignorez cet email."
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@seven.ai'),
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.exception("SMTP error while sending OTP")
        otp.delete()
        return None, str(exc)
    return otp, None


def _get_active_otp(user, purpose):
    return EmailOTP.objects.filter(
        user=user,
        purpose=purpose,
        consumed_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).order_by('-created_at').first()


@never_cache
def verify_signup_otp(request):
    user_id = request.session.get('pending_signup_user_id')
    if not user_id:
        messages.error(request, "Aucune inscription en attente de verification OTP.")
        return redirect('register')

    user = User.objects.filter(pk=user_id).first()
    if not user:
        request.session.pop('pending_signup_user_id', None)
        messages.error(request, "Compte introuvable. Recommencez l'inscription.")
        return redirect('register')

    otp = _get_active_otp(user, EmailOTP.PURPOSE_SIGNUP)
    if not otp:
        otp, _ = _create_and_send_otp(user, EmailOTP.PURPOSE_SIGNUP, user.email)
        if not otp:
            messages.error(request, "Impossible de renvoyer l'OTP. Contactez un administrateur.")
            return redirect('register')
        messages.info(request, "Un nouveau code OTP a ete envoye.")

    if request.method == 'POST':
        code = request.POST.get('otp_code', '').strip()
        if not code:
            return render(request, 'registration/verify_otp.html', {'purpose': 'signup', 'otp_error': "Saisissez le code OTP."})

        if otp.attempts >= otp.max_attempts:
            messages.error(request, "Trop de tentatives OTP. Un nouveau code a ete envoye.")
            otp.delete()
            _create_and_send_otp(user, EmailOTP.PURPOSE_SIGNUP, user.email)
            return redirect('verify_signup_otp')

        otp.attempts += 1
        otp.save(update_fields=['attempts'])
        if not check_password(code, otp.code_hash):
            return render(request, 'registration/verify_otp.html', {'purpose': 'signup', 'otp_error': "Code OTP invalide."})

        otp.consumed_at = timezone.now()
        otp.save(update_fields=['consumed_at'])
        user.is_active = True
        user.save(update_fields=['is_active'])
        request.session.pop('pending_signup_user_id', None)
        login(request, user)
        messages.success(request, "Inscription validee avec succes.")
        return redirect('dashboard_user')

    return render(request, 'registration/verify_otp.html', {'purpose': 'signup'})


@never_cache
def verify_login_otp(request):
    user_id = request.session.get('pending_login_user_id')
    if not user_id:
        messages.error(request, "Aucune connexion en attente de verification OTP.")
        return redirect('login')

    user = User.objects.filter(pk=user_id, is_active=True).first()
    if not user:
        request.session.pop('pending_login_user_id', None)
        messages.error(request, "Utilisateur introuvable.")
        return redirect('login')

    otp = _get_active_otp(user, EmailOTP.PURPOSE_LOGIN)
    if not otp:
        otp, _ = _create_and_send_otp(user, EmailOTP.PURPOSE_LOGIN, user.email)
        if not otp:
            messages.error(request, "Impossible de renvoyer l'OTP. Reessayez plus tard.")
            return redirect('login')
        messages.info(request, "Un nouveau code OTP a ete envoye.")

    if request.method == 'POST':
        code = request.POST.get('otp_code', '').strip()
        if not code:
            return render(request, 'registration/verify_otp.html', {'purpose': 'login', 'otp_error': "Saisissez le code OTP."})

        if otp.attempts >= otp.max_attempts:
            messages.error(request, "Trop de tentatives OTP. Un nouveau code a ete envoye.")
            otp.delete()
            _create_and_send_otp(user, EmailOTP.PURPOSE_LOGIN, user.email)
            return redirect('verify_login_otp')

        otp.attempts += 1
        otp.save(update_fields=['attempts'])
        if not check_password(code, otp.code_hash):
            return render(request, 'registration/verify_otp.html', {'purpose': 'login', 'otp_error': "Code OTP invalide."})

        otp.consumed_at = timezone.now()
        otp.save(update_fields=['consumed_at'])
        request.session.pop('pending_login_user_id', None)
        next_url = request.session.pop('pending_next_url', '')
        login(request, user)
        if next_url:
            return redirect(next_url)
        return redirect('dashboard_admin' if is_admin(user) else 'dashboard_user')

    return render(request, 'registration/verify_otp.html', {'purpose': 'login'})


@never_cache
@require_POST
def resend_otp(request, purpose):
    if purpose == EmailOTP.PURPOSE_SIGNUP:
        user_id = request.session.get('pending_signup_user_id')
        redirect_name = 'verify_signup_otp'
    elif purpose == EmailOTP.PURPOSE_LOGIN:
        user_id = request.session.get('pending_login_user_id')
        redirect_name = 'verify_login_otp'
    else:
        return redirect('login')

    user = User.objects.filter(pk=user_id).first()
    if not user:
        messages.error(request, "Session OTP invalide.")
        return redirect('login')

    otp, _ = _create_and_send_otp(user, purpose, user.email)
    if otp:
        messages.success(request, "Un nouveau code OTP a ete envoye par email.")
    else:
        messages.error(request, "Echec de l'envoi OTP. Reessayez.")
    return redirect(redirect_name)


@never_cache
@require_POST
def custom_logout(request):
    """Déconnexion avec invalidation de session"""
    request.session.flush()
    logout(request)
    response = redirect('login')
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response
