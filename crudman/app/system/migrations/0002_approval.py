import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("system", "0001_initial"),
    ]

    operations = [
        # The engine's grant in 0001 is on the table, so it covers this column without a
        # second one -- and the engine needs it: it reports the outcome of a plan for one
        # environment while another may be running for the same commit.
        migrations.AddField(
            model_name="deployment",
            name="environment",
            field=models.CharField(default="prod", editable=False, max_length=40),
        ),
        migrations.CreateModel(
            name="Approval",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("approved", models.BooleanField(blank=True, null=True)),
                ("comment", models.TextField(blank=True, default="")),
                ("decided_on", models.DateTimeField(blank=True, null=True)),
                ("deployment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="approvals", to="system.deployment")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="approval",
            constraint=models.UniqueConstraint(fields=("deployment", "user"), name="one_approval_per_reviewer"),
        ),
    ]
