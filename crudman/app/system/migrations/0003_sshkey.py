import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("system", "0002_approval"),
    ]

    operations = [
        migrations.CreateModel(
            name="SSHKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(blank=True, default="", max_length=100)),
                ("public_key", models.TextField(editable=False)),
                ("fingerprint", models.CharField(editable=False, max_length=100, unique=True)),
                ("added_on", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ssh_keys", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "SSH key",
                "verbose_name_plural": "SSH keys",
                "ordering": ("added_on",),
            },
        ),
    ]
