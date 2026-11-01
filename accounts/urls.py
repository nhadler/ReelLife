"""URLs for signing in and out."""

from django.contrib.auth.decorators import login_not_required
from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", login_not_required(views.login_page), name="login"),
    path(
        "oidc/start/",
        login_not_required(views.LoginStartView.as_view()),
        name="oidc_start",
    ),
    path(
        "oidc/callback/",
        login_not_required(views.CallbackView.as_view()),
        name="oidc_callback",
    ),
    path(
        "local/",
        login_not_required(views.local_login),
        name="local_login",
    ),
    # Open to anyone so a stale tab's "Sign out" lands on the login page
    # instead of a login redirect back to logout.
    path("logout/", login_not_required(views.logout_view), name="logout"),
    path("picture/", views.picture, name="picture"),
]
