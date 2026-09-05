from django import forms

from .models import Tenant
from .utils import slugify_tenant_name


class TenantCreationForm(forms.ModelForm):
    """Form for creating a tenant in the admin.

    ``clean`` derives the slug for the role and bronze schema from the human name, so the
    slug field is not shown. The password is not stored on the model, only in PostgreSQL.
    """

    password = forms.CharField(
        widget=forms.PasswordInput,
        min_length=8,
        help_text="Login password for the tenant's database role (at least 8 characters).",
    )

    class Meta:
        model = Tenant
        fields = [
            "display_name",
            "connection_limit",
            "statement_timeout",
            "work_mem",
            "temp_file_limit",
        ]

    def clean(self):
        cleaned = super().clean()
        display_name = cleaned.get("display_name", "")
        slug = slugify_tenant_name(display_name)
        if not slug:
            # Nothing left to name the role and schema with.
            self.add_error(
                "display_name", "Could not derive a valid identifier from this name."
            )
        elif Tenant.objects.filter(pk=slug).exists():
            self.add_error(
                "display_name", f"A tenant with the identifier '{slug}' already exists."
            )
        else:
            # The slug is the model's primary key, so save() needs it set here.
            cleaned["name"] = slug
            self.instance.name = slug
        return cleaned


class TenantChangeForm(forms.ModelForm):
    """Form for editing an existing tenant.

    The slug names the role and schema and so is read-only; the human name and the
    resource limits are editable, the password is not.
    """

    class Meta:
        model = Tenant
        fields = [
            "name",
            "display_name",
            "connection_limit",
            "statement_timeout",
            "work_mem",
            "temp_file_limit",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].disabled = True
