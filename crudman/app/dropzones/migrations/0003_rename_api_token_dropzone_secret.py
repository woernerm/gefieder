# The SFTP upload method: the api_token becomes the method-agnostic "secret" (a dropzone
# has exactly one upload method, so the one field carries the Bearer token of an API
# dropzone or the SFTP password of an SFTP dropzone; a rename keeps the tokens of
# existing API dropzones), and every dropzone gains a default validity for its uploads.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dropzones", "0002_dropzone_api_token_alter_dropzone_upload_method"),
    ]

    operations = [
        migrations.RenameField(
            model_name="dropzone",
            old_name="api_token",
            new_name="secret",
        ),
        migrations.AlterField(
            model_name="dropzone",
            name="secret",
            field=models.CharField(
                blank=True,
                max_length=64,
                help_text=(
                    "Secret an unattended client presents: the API endpoint expects it "
                    "as an 'Authorization: Bearer <secret>' header, the SFTP upload "
                    "uses it as the login password. For the API it may stay empty to "
                    "keep the endpoint open (only sensible without a login "
                    "requirement); an SFTP dropzone without a secret accepts no logins."
                ),
            ),
        ),
        migrations.AddField(
            model_name="dropzone",
            name="default_validity",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("until_replaced", "Valid from now on until replacement"),
                    ("always", "Valid for past and future until replacement"),
                    ("period", "Valid for a given time period"),
                ],
                default="until_replaced",
                help_text=(
                    "Validity preselected on the browser upload page (the uploader "
                    "may change it there) and applied as-is to API uploads that send "
                    "no validity and to SFTP uploads. A given time period needs dates "
                    "from the uploader, so it is only available for the browser "
                    "upload."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="dropzone",
            name="name",
            field=models.CharField(
                max_length=100,
                unique=True,
                help_text=(
                    "Identifies the dropzone, also in analytics queries and as the "
                    "SFTP login name. e.g. bank-exports."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="dropzone",
            name="upload_method",
            field=models.CharField(
                max_length=10,
                choices=[
                    ("browser", "Browser upload"),
                    ("api", "API endpoint"),
                    ("sftp", "SFTP"),
                ],
                default="browser",
                help_text="How the files arrive.",
            ),
        ),
    ]
