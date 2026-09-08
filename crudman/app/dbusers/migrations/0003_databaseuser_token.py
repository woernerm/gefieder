from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dbusers", "0002_databaseuser_awaiting_credential"),
    ]

    operations = [
        migrations.AddField(
            model_name="databaseuser",
            name="token",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text="Authenticates a developer's checkout when it fetches a password.",
                max_length=64,
                verbose_name="access token",
            ),
        ),
    ]
