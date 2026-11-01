"""Forms for accounts."""

from django import forms


class BreakglassForm(forms.Form):
    """Local admin sign-in: username, password and TOTP code."""

    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput, max_length=256)
    otp_token = forms.RegexField(
        label="Authenticator code",
        regex=r"^\d{6}$",
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}
        ),
    )
