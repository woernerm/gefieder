from django import forms

from .models import Tenant
from .utils import slugify_tenant_name


class TenantCreationForm(forms.ModelForm):
    """Form for creating a tenant in the admin.

    The user types a human name like "Project A"; the matching PostgreSQL-safe slug
    (project_a) is derived from it and used as the role and bronze schema name. The slug
    field itself is not shown on the add page -- it is filled in clean() from the name -- so
    the user never has to know the slugging rules. A tenant role also needs a login
    password, which is not stored on the model (it lives in PostgreSQL only).
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
            # Everything was stripped away (e.g. the name was only punctuation), so there
            # is no valid identifier to use for the role and schema.
            self.add_error(
                "display_name", "Could not derive a valid identifier from this name."
            )
        elif Tenant.objects.filter(pk=slug).exists():
            self.add_error(
                "display_name", f"A tenant with the identifier '{slug}' already exists."
            )
        else:
            # name is the model's primary key (the slug); set it so save() persists it.
            cleaned["name"] = slug
            self.instance.name = slug
        return cleaned


class TenantChangeForm(forms.ModelForm):
    """Form for editing an existing tenant.

    The slug identifies the role and schema and cannot be changed, so it is shown
    read-only. The human name and the resource limits are editable; the password is not.
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
