from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("dbusers", "0004_accesstoken"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="databaseuser",
            name="awaiting_credential",
        ),
    ]
