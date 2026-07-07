# The webhook upload method: a device that can only call a URL (e.g. a Shelly relay
# reporting a temperature) sends its readings as query parameters of an HTTP GET, and
# each call is stored as a one-row CSV file through the usual pipeline. Only the method
# choices and help texts change; no data is touched.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dropzones", "0003_rename_api_token_dropzone_secret"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dropzone",
            name="upload_method",
            field=models.CharField(
                max_length=10,
                choices=[
                    ("browser", "Browser upload"),
                    ("api", "API endpoint"),
                    ("sftp", "SFTP"),
                    ("webhook", "Webhook (HTTP GET)"),
                ],
                default="browser",
                help_text="How the files arrive.",
            ),
        ),
        migrations.AlterField(
            model_name="dropzone",
            name="secret",
            field=models.CharField(
                blank=True,
                max_length=64,
                help_text=(
                    "Secret an unattended client presents: the API endpoint and the "
                    "webhook expect it as an 'Authorization: Bearer <secret>' header, "
                    "the SFTP upload uses it as the login password. For the API and "
                    "the webhook it may stay empty to keep the endpoint open (only "
                    "sensible without a login requirement); an SFTP dropzone without "
                    "a secret accepts no logins."
                ),
            ),
        ),
        migrations.AlterField(
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
                    "no validity and to SFTP and webhook uploads. A given time period "
                    "needs dates from the uploader, so it is only available for the "
                    "browser upload."
                ),
            ),
        ),
    ]
