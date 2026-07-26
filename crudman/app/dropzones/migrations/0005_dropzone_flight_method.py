# The Arrow Flight upload method: a client sends one or more Arrow tables over gRPC and
# each table is stored as a parquet file through the usual pipeline. Like the SFTP
# method it authenticates with the dropzone's name and secret, hence the extended help
# text. Only the method choices and help texts change; no data is touched.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dropzones', '0004_dropzone_webhook_method'),
    ]

    operations = [
        migrations.AlterField(
            model_name='dropzone',
            name='secret',
            field=models.CharField(blank=True, help_text="Secret an unattended client presents: the API endpoint and the webhook expect it as an 'Authorization: Bearer <secret>' header, the SFTP and Arrow Flight uploads use it as the login password. For the API and the webhook it may stay empty to keep the endpoint open (only sensible without a login requirement); an SFTP or Arrow Flight dropzone without a secret accepts no logins.", max_length=64),
        ),
        migrations.AlterField(
            model_name='dropzone',
            name='upload_method',
            field=models.CharField(choices=[('browser', 'Browser upload'), ('api', 'API endpoint'), ('sftp', 'SFTP'), ('webhook', 'Webhook (HTTP GET)'), ('flight', 'Arrow Flight')], default='browser', help_text='How the files arrive.', max_length=10),
        ),
    ]
