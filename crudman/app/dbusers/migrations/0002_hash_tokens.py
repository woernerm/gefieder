from django.db import migrations


def hash_tokens(apps, schema_editor):
    """Replace every token by its hash, so the ones in use keep working."""
    from dbusers.utils import hash_token

    for record in apps.get_model("dbusers", "DatabaseUser").objects.exclude(token=""):
        record.token = hash_token(record.token)
        record.save(update_fields=["token"])


class Migration(migrations.Migration):

    dependencies = [("dbusers", "0001_initial")]

    # Without a reverse: a hash cannot be turned back into its token.
    operations = [migrations.RunPython(hash_tokens)]
