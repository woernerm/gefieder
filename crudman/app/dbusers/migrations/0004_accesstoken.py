from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("dbusers", "0003_databaseuser_token"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="AccessToken",
            fields=[],
            options={
                "verbose_name": "access token",
                "verbose_name_plural": "Access token",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("dbusers.databaseuser",),
        ),
    ]
