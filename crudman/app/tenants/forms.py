from django import forms

from .models import Tenant


class TenantCreationForm(forms.ModelForm):
    """Form for creating a tenant in the admin.

    A tenant role needs a login password, which is not stored on the model (it lives in
    PostgreSQL only). The field is added here so the create page can collect it and the
    admin can pass it to the create_tenant database function.
    """

    password = forms.CharField(
        widget=forms.PasswordInput,
        min_length=8,
        help_text="Login password for the tenant's database role (at least 8 characters).",
    )

    class Meta:
        model = Tenant
        fields = [
            "name",
            "connection_limit",
            "statement_timeout",
            "work_mem",
            "temp_file_limit",
        ]


class TenantChangeForm(forms.ModelForm):
    """Form for editing an existing tenant's resource limits.

    The name identifies the role and schema and cannot be changed, so it is shown
    read-only. The password is not editable here; only the resource limits are.
    """

    class Meta:
        model = Tenant
        fields = [
            "name",
            "connection_limit",
            "statement_timeout",
            "work_mem",
            "temp_file_limit",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].disabled = True
