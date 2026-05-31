from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordChangeForm
from .models import Fichier, Dossier, UserProfile, PME, DemandeMotDePasse, DemandeAugmentationQuota
from .utils.pme import normalize_pme_nom


class UploadFichierForm(forms.ModelForm):
    class Meta:
        model = Fichier
        fields = ['nom', 'fichier', 'dossier']
        widgets = {
            'nom': forms.TextInput(attrs={
                'class': 'w-full bg-navy-900/80 border border-white/08 rounded-lg px-4 py-2 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-emerald-500/40',
                'placeholder': 'Nom du fichier'
            }),
            'fichier': forms.FileInput(attrs={
                'class': 'w-full bg-navy-900/80 border border-white/08 rounded-lg px-4 py-2 text-sm text-slate-300',
                'accept': '*/*'
            }),
            'dossier': forms.Select(attrs={
                'class': 'w-full bg-navy-900/80 border border-white/08 rounded-lg px-4 py-2 text-sm text-slate-300 focus:outline-none focus:border-emerald-500/40'
            })
        }
    
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['dossier'].queryset = Dossier.objects.filter(proprietaire=user)
            self.fields['dossier'].empty_label = "Aucun dossier (racine)"
    
    def save(self, commit=True, user=None):
        instance = super().save(commit=False)
        if user:
            instance.proprietaire = user
            # Calculer la taille du fichier
            if instance.fichier:
                instance.fichier.seek(0)
                instance.taille = instance.fichier.size
                instance.fichier.seek(0)
        if commit:
            instance.save()
        return instance


class CreerDossierForm(forms.ModelForm):
    class Meta:
        model = Dossier
        fields = ['nom', 'dossier_parent']
        widgets = {
            'nom': forms.TextInput(attrs={
                'class': 'w-full bg-navy-900/80 border border-white/08 rounded-lg px-4 py-2 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-emerald-500/40',
                'placeholder': 'Nom du dossier'
            }),
            'dossier_parent': forms.Select(attrs={
                'class': 'w-full bg-navy-900/80 border border-white/08 rounded-lg px-4 py-2 text-sm text-slate-300 focus:outline-none focus:border-emerald-500/40'
            })
        }
    
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['dossier_parent'].queryset = Dossier.objects.filter(proprietaire=user)
            self.fields['dossier_parent'].empty_label = "Dossier racine"
    
    def save(self, commit=True, user=None):
        instance = super().save(commit=False)
        if user:
            instance.proprietaire = user
        if commit:
            instance.save()
        return instance


PHOTO_WIDGET = forms.FileInput(attrs={
    'class': 'admin-input',
    'accept': 'image/jpeg,image/png,image/webp,image/gif',
})


class ProfilePhotoForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['photo']
        widgets = {'photo': PHOTO_WIDGET}


class PMEForm(forms.ModelForm):
    class Meta:
        model = PME
        fields = [
            'nom', 'secteur', 'email_contact', 'telephone', 'adresse',
            'quota_stockage', 'a_repondu', 'photo',
        ]
        widgets = {
            'nom': forms.TextInput(attrs={'class': 'admin-input'}),
            'secteur': forms.TextInput(attrs={'class': 'admin-input'}),
            'email_contact': forms.EmailInput(attrs={'class': 'admin-input'}),
            'telephone': forms.TextInput(attrs={'class': 'admin-input'}),
            'adresse': forms.Textarea(attrs={'class': 'admin-input', 'rows': 3}),
            'quota_stockage': forms.NumberInput(attrs={'class': 'admin-input', 'min': 1}),
            'a_repondu': forms.CheckboxInput(attrs={'class': 'admin-checkbox'}),
            'photo': PHOTO_WIDGET,
        }

    def clean_nom(self):
        nom = self.cleaned_data['nom'].strip()
        norm = normalize_pme_nom(nom)
        qs = PME.objects.filter(nom_normalise=norm)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            existing = qs.first()
            raise forms.ValidationError(
                f'Une PME « {existing.nom} » existe déjà. '
                'Rattachez les utilisateurs à celle-ci au lieu d\'en créer une nouvelle.'
            )
        return nom


class AdminUserForm(forms.ModelForm):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': 'admin-input'}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': 'admin-input'}))
    first_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'admin-input'}))
    last_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'admin-input'}))
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'class': 'admin-input'}),
        help_text="Laisser vide pour conserver le mot de passe actuel.",
    )
    role = forms.ChoiceField(choices=UserProfile._meta.get_field('role').choices, widget=forms.Select(attrs={'class': 'admin-input'}))
    pme = forms.ModelChoiceField(queryset=PME.objects.all(), required=False, empty_label="— Aucune PME —", widget=forms.Select(attrs={'class': 'admin-input'}))
    quota_stockage = forms.IntegerField(min_value=1, initial=50, widget=forms.NumberInput(attrs={'class': 'admin-input'}))
    service = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'admin-input'}))
    is_active = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs={'class': 'admin-checkbox'}))
    photo = forms.ImageField(required=False, widget=PHOTO_WIDGET)

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            profile = getattr(self.instance, 'profile', None)
            if profile:
                self.fields['role'].initial = profile.role
                self.fields['pme'].initial = profile.pme_id
                self.fields['quota_stockage'].initial = profile.quota_stockage
                self.fields['service'].initial = profile.service
                if profile.photo:
                    self.fields['photo'].initial = profile.photo
            self.fields['password'].required = False
        else:
            self.fields['password'].required = True

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = self.cleaned_data.get('is_active', True)
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)
        if commit:
            user.save()
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = self.cleaned_data['role']
            profile.pme = self.cleaned_data.get('pme')
            profile.quota_stockage = self.cleaned_data['quota_stockage']
            profile.service = self.cleaned_data.get('service', '')
            photo = self.cleaned_data.get('photo')
            if photo:
                profile.photo = photo
            profile.save()
        return user


class AdminAccountForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'admin-input'}),
            'email': forms.EmailInput(attrs={'class': 'admin-input'}),
            'first_name': forms.TextInput(attrs={'class': 'admin-input'}),
            'last_name': forms.TextInput(attrs={'class': 'admin-input'}),
        }


class AdminPasswordForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'admin-input'})


USER_INPUT = 'user-input'


class UserAccountForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name']
        widgets = {
            'username': forms.TextInput(attrs={'class': USER_INPUT}),
            'email': forms.EmailInput(attrs={'class': USER_INPUT}),
            'first_name': forms.TextInput(attrs={'class': USER_INPUT}),
            'last_name': forms.TextInput(attrs={'class': USER_INPUT}),
        }


class UserPasswordForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': USER_INPUT})


class UserProfileSettingsForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['service']
        widgets = {
            'service': forms.TextInput(attrs={
                'class': USER_INPUT,
                'placeholder': 'Ex. Comptabilité, RH…',
            }),
        }


class DemandeMotDePasseForm(forms.ModelForm):
    class Meta:
        model = DemandeMotDePasse
        fields = ['message']
        widgets = {
            'message': forms.Textarea(attrs={
                'class': USER_INPUT,
                'rows': 3,
                'placeholder': 'Précisez la raison de votre demande (optionnel)…',
            }),
        }
        labels = {'message': 'Message pour l\'administrateur'}


class DemandeAugmentationQuotaForm(forms.ModelForm):
    class Meta:
        model = DemandeAugmentationQuota
        fields = ['quota_demande', 'message']
        widgets = {
            'quota_demande': forms.NumberInput(attrs={
                'class': USER_INPUT,
                'min': 1,
                'placeholder': 'Ex. 100',
            }),
            'message': forms.Textarea(attrs={
                'class': USER_INPUT,
                'rows': 3,
                'placeholder': 'Justifiez votre besoin d\'espace supplémentaire (optionnel)…',
            }),
        }
        labels = {
            'quota_demande': 'Quota souhaité (Go)',
            'message': 'Message pour l\'administrateur',
        }

    def __init__(self, *args, quota_actuel=50, **kwargs):
        super().__init__(*args, **kwargs)
        self.quota_actuel = quota_actuel
        self.fields['quota_demande'].help_text = f'Quota actuel : {quota_actuel} Go'

    def clean_quota_demande(self):
        quota_demande = self.cleaned_data['quota_demande']
        if quota_demande <= self.quota_actuel:
            raise forms.ValidationError(
                f'Le quota demandé doit être supérieur à votre quota actuel ({self.quota_actuel} Go).'
            )
        return quota_demande


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['quota_stockage', 'role', 'service', 'deux_facteurs_actif', 'pme']
        widgets = {
            'quota_stockage': forms.NumberInput(attrs={
                'class': 'w-full bg-navy-900/80 border border-white/08 rounded-lg px-4 py-2 text-sm text-slate-300 focus:outline-none focus:border-emerald-500/40',
                'min': '1'
            }),
            'role': forms.Select(attrs={
                'class': 'w-full bg-navy-900/80 border border-white/08 rounded-lg px-4 py-2 text-sm text-slate-300 focus:outline-none focus:border-emerald-500/40'
            }),
            'service': forms.TextInput(attrs={
                'class': 'w-full bg-navy-900/80 border border-white/08 rounded-lg px-4 py-2 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-emerald-500/40',
                'placeholder': 'Service'
            }),
            'deux_facteurs_actif': forms.CheckboxInput(attrs={
                'class': 'w-5 h-5 rounded border-white/20 bg-navy-900 text-emerald-500 focus:ring-emerald-500'
            })
        }
