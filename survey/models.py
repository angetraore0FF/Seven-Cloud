from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.utils import timezone


class PME(models.Model):
    nom = models.CharField(max_length=200)
    nom_normalise = models.CharField(
        max_length=200,
        unique=True,
        editable=False,
        help_text="Nom normalisé pour éviter les doublons (SEVEN AI = SEVEN_AI).",
    )
    secteur = models.CharField(max_length=100)
    email_contact = models.EmailField()
    telephone = models.CharField(max_length=20)
    adresse = models.TextField()
    date_creation = models.DateTimeField(auto_now_add=True)
    a_repondu = models.BooleanField(default=False)
    photo = models.ImageField(upload_to='photos/pme/%Y/', blank=True, null=True)
    quota_stockage = models.IntegerField(
        default=200,
        validators=[MinValueValidator(1)],
        help_text="Quota de stockage partagé pour toute la PME (Go)",
    )

    class Meta:
        verbose_name = "PME"
        verbose_name_plural = "PMEs"

    def __str__(self):
        return self.nom

    def save(self, *args, **kwargs):
        from .utils.pme import normalize_pme_nom

        self.nom_normalise = normalize_pme_nom(self.nom)
        super().save(*args, **kwargs)

    def espace_utilise_octets(self):
        from django.db.models import Sum
        return (
            Fichier.objects.filter(
                proprietaire__profile__pme=self,
                est_corbeille=False,
            ).aggregate(total=Sum('taille'))['total']
            or 0
        )

    def espace_utilise_go(self):
        return round(self.espace_utilise_octets() / (1024 ** 3), 3)

    def pourcentage_utilise(self):
        from .utils.storage import go_to_bytes, pourcentage
        return pourcentage(self.espace_utilise_octets(), go_to_bytes(self.quota_stockage))


class Question(models.Model):
    texte = models.TextField()
    categorie = models.CharField(max_length=100, choices=[
        ('general', 'Général'),
        ('financier', 'Financier'),
        ('operationnel', 'Opérationnel'),
        ('rh', 'Ressources Humaines'),
    ])
    ordre = models.IntegerField(default=0)
    obligatoire = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "Question"
        verbose_name_plural = "Questions"
        ordering = ['ordre']
    
    def __str__(self):
        return f"{self.ordre}. {self.texte[:50]}..."


class Reponse(models.Model):
    pme = models.ForeignKey(PME, on_delete=models.CASCADE, related_name='reponses')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='reponses')
    texte_reponse = models.TextField()
    date_reponse = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Réponse"
        verbose_name_plural = "Réponses"
        unique_together = ['pme', 'question']
    
    def __str__(self):
        return f"{self.pme.nom} - {self.question.texte[:30]}"


class UserProfile(models.Model):
    MODE_STOCKAGE_LOCAL = 'local'
    MODE_STOCKAGE_DISTANCE = 'distance'
    MODE_STOCKAGE_CHOICES = [
        (MODE_STOCKAGE_LOCAL, 'Local'),
        (MODE_STOCKAGE_DISTANCE, 'À distance'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    pme = models.ForeignKey(
        PME, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='utilisateurs', verbose_name="PME"
    )
    quota_stockage = models.IntegerField(default=50, validators=[MinValueValidator(1)], help_text="Quota en Go")
    role = models.CharField(max_length=20, choices=[
        ('admin', 'Administrateur'),
        ('employe', 'Employé'),
        ('rh', 'RH'),
        ('compta', 'Comptabilité'),
        ('invite', 'Invité'),
    ], default='employe')
    service = models.CharField(max_length=100, blank=True)
    mode_stockage = models.CharField(
        max_length=10,
        choices=MODE_STOCKAGE_CHOICES,
        default=MODE_STOCKAGE_DISTANCE,
        verbose_name="Mode de stockage",
    )
    deux_facteurs_actif = models.BooleanField(default=False)
    photo = models.ImageField(upload_to='photos/profils/%Y/', blank=True, null=True)
    
    class Meta:
        verbose_name = "Profil utilisateur"
        verbose_name_plural = "Profils utilisateurs"
    
    def __str__(self):
        return f"{self.user.username} - {self.role}"
    
    def espace_utilise_octets(self):
        from django.db.models import Sum
        return (
            self.user.fichiers.filter(est_corbeille=False)
            .aggregate(total=Sum('taille'))['total']
            or 0
        )

    def espace_utilise(self):
        """Espace utilisé en Go (hors corbeille)."""
        return round(self.espace_utilise_octets() / (1024 ** 3), 3)

    def pourcentage_utilise(self):
        from .utils.storage import go_to_bytes, pourcentage
        return pourcentage(self.espace_utilise_octets(), go_to_bytes(self.quota_stockage))

    def utilise_stockage_distance(self):
        from django.conf import settings
        return (
            settings.NEXTCLOUD_ENABLED
            and self.mode_stockage == self.MODE_STOCKAGE_DISTANCE
        )


class DemandeMotDePasse(models.Model):
    """Demande de réinitialisation de mot de passe soumise par un utilisateur."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='demandes_mdp')
    message = models.TextField(blank=True, help_text="Motif ou précisions de l'utilisateur")
    date_demande = models.DateTimeField(auto_now_add=True)
    traite = models.BooleanField(default=False)
    date_traitement = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Demande de mot de passe"
        verbose_name_plural = "Demandes de mot de passe"
        ordering = ['-date_demande']

    def __str__(self):
        statut = "traitée" if self.traite else "en attente"
        return f"{self.user.username} — {statut}"


class DemandeAugmentationQuota(models.Model):
    """Demande d'augmentation du quota de stockage (Go)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='demandes_quota')
    quota_actuel = models.IntegerField(help_text="Quota en Go au moment de la demande")
    quota_demande = models.IntegerField(validators=[MinValueValidator(1)], help_text="Quota souhaité en Go")
    message = models.TextField(blank=True)
    date_demande = models.DateTimeField(auto_now_add=True)
    traite = models.BooleanField(default=False)
    date_traitement = models.DateTimeField(null=True, blank=True)
    quota_accorde = models.IntegerField(null=True, blank=True, help_text="Quota accordé par l'admin en Go")

    class Meta:
        verbose_name = "Demande d'augmentation de quota"
        verbose_name_plural = "Demandes d'augmentation de quota"
        ordering = ['-date_demande']

    def __str__(self):
        statut = "traitée" if self.traite else "en attente"
        return f"{self.user.username} : {self.quota_actuel} → {self.quota_demande} Go ({statut})"


class EmailOTP(models.Model):
    PURPOSE_SIGNUP = 'signup'
    PURPOSE_LOGIN = 'login'
    PURPOSE_CHOICES = [
        (PURPOSE_SIGNUP, 'Inscription'),
        (PURPOSE_LOGIN, 'Connexion'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_otps')
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "OTP email"
        verbose_name_plural = "OTPs email"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.purpose}"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_consumed(self):
        return self.consumed_at is not None


class Dossier(models.Model):
    nom = models.CharField(max_length=200)
    proprietaire = models.ForeignKey(User, on_delete=models.CASCADE, related_name='dossiers')
    dossier_parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='sous_dossiers')
    date_creation = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Dossier"
        verbose_name_plural = "Dossiers"
    
    def __str__(self):
        return self.nom
    
    def taille_totale(self):
        """Calcule la taille totale du dossier en octets"""
        total = sum(f.taille for f in self.fichiers.all())
        for sous_dossier in self.sous_dossiers.all():
            total += sous_dossier.taille_totale()
        return total
    
    def nombre_fichiers(self):
        """Compte le nombre total de fichiers dans le dossier"""
        count = self.fichiers.count()
        for sous_dossier in self.sous_dossiers.all():
            count += sous_dossier.nombre_fichiers()
        return count


class PartageFichier(models.Model):
    """Lien de partage entre un fichier et un destinataire, avec mot de passe optionnel."""
    fichier = models.ForeignKey('Fichier', on_delete=models.CASCADE, related_name='partages')
    destinataire = models.ForeignKey(User, on_delete=models.CASCADE, related_name='partages_recus')
    mot_de_passe = models.CharField(max_length=128, blank=True)
    date_partage = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Partage de fichier"
        verbose_name_plural = "Partages de fichiers"
        unique_together = ('fichier', 'destinataire')

    def __str__(self):
        return f"{self.fichier.nom} → {self.destinataire.username}"

    @property
    def protege_par_mot_de_passe(self):
        return bool(self.mot_de_passe)

    def definir_mot_de_passe(self, raw_password):
        from django.contrib.auth.hashers import make_password
        self.mot_de_passe = make_password(raw_password) if raw_password else ''

    def verifier_mot_de_passe(self, raw_password):
        if not self.mot_de_passe:
            return True
        from django.contrib.auth.hashers import check_password
        return check_password(raw_password, self.mot_de_passe)


class Fichier(models.Model):
    nom = models.CharField(max_length=255)
    fichier = models.FileField(upload_to='fichiers/%Y/%m/', blank=True)
    nextcloud_path = models.CharField(max_length=512, blank=True)
    nextcloud_etag = models.CharField(max_length=128, blank=True)
    stockage_externe = models.BooleanField(default=False)
    proprietaire = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fichiers')
    dossier = models.ForeignKey(Dossier, on_delete=models.SET_NULL, null=True, blank=True, related_name='fichiers')
    taille = models.BigIntegerField(default=0)  # Taille en octets
    type_mime = models.CharField(max_length=100, blank=True)
    chiffre = models.BooleanField(default=True)
    date_upload = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    partage_avec = models.ManyToManyField(
        User,
        through='PartageFichier',
        blank=True,
        related_name='fichiers_partages',
    )
    est_favori = models.BooleanField(default=False)
    est_corbeille = models.BooleanField(default=False)
    
    class Meta:
        verbose_name = "Fichier"
        verbose_name_plural = "Fichiers"
        ordering = ['-date_upload']
    
    def __str__(self):
        return self.nom
    
    def extension(self):
        """Retourne l'extension du fichier"""
        return self.nom.split('.')[-1].lower() if '.' in self.nom else ''
    
    def taille_formatee(self):
        """Retourne la taille formatée (Ko, Mo, Go)"""
        taille = self.taille
        for unite in ['o', 'Ko', 'Mo', 'Go']:
            if taille < 1024:
                return f"{round(taille, 1)} {unite}"
            taille /= 1024
        return f"{round(taille, 1)} To"

    @property
    def est_sur_nextcloud(self):
        return self.stockage_externe and bool(self.nextcloud_path)
