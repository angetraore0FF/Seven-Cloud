"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from survey.views import (
    router_dashboard, dashboard_user, user_fichiers, user_partages, user_parametres,
    api_upload_fichier, telecharger_fichier,
    dashboard_admin, admin_pme, admin_utilisateurs, admin_securite, admin_parametres,
    custom_login, custom_register, custom_logout,
    verify_signup_otp, verify_login_otp, resend_otp,
)

urlpatterns = [
    path("", router_dashboard, name="router_dashboard"),
    path("login/", custom_login, name="login"),
    path("register/", custom_register, name="register"),
    path("register/verify-otp/", verify_signup_otp, name="verify_signup_otp"),
    path("logout/", custom_logout, name="logout"),
    path("login/verify-otp/", verify_login_otp, name="verify_login_otp"),
    path("otp/resend/<str:purpose>/", resend_otp, name="resend_otp"),
    path("dashboard/", dashboard_user, name="dashboard_user"),
    path("dashboard/fichiers/", user_fichiers, name="user_fichiers"),
    path("api/fichiers/upload/", api_upload_fichier, name="api_upload_fichier"),
    path("fichiers/<int:pk>/telecharger/", telecharger_fichier, name="telecharger_fichier"),
    path("dashboard/partages/", user_partages, name="user_partages"),
    path("dashboard/parametres/", user_parametres, name="user_parametres"),
    path("admin/dashboard/", dashboard_admin, name="dashboard_admin"),
    path("admin/pme/", admin_pme, name="admin_pme"),
    path("admin/utilisateurs/", admin_utilisateurs, name="admin_utilisateurs"),
    path("admin/securite/", admin_securite, name="admin_securite"),
    path("admin/parametres/", admin_parametres, name="admin_parametres"),
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
