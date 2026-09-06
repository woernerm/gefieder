import os

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def grant_to_engine(apps, schema_editor):
    """Let the engine close the deployment row crudman opened for it.

    The engine runs in another container with no Django and no crudman, so it writes this
    one table directly. SELECT and UPDATE only, and on this table alone: it reports the
    outcome of a deployment, it does not decide one.

    The role name comes from the environment for the same reason every other one does --
    SQLMESH_DB_USER in buildtime.env may name a role a shared cluster already has.
    """
    role = os.environ.get("SQLMESH_DB_USER", "sqlmesh")
    schema_editor.execute(
        f"GRANT SELECT, UPDATE ON system_deployment TO {schema_editor.quote_name(role)}"
    )


def revoke_from_engine(apps, schema_editor):
    role = os.environ.get("SQLMESH_DB_USER", "sqlmesh")
    schema_editor.execute(
        f"REVOKE SELECT, UPDATE ON system_deployment FROM {schema_editor.quote_name(role)}"
    )


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Deployment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sha', models.CharField(editable=False, max_length=40, verbose_name='commit')),
                ('main_sha', models.CharField(editable=False, max_length=40)),
                ('pinned', models.BooleanField(default=False, editable=False, help_text='A person chose this commit; the poll did not.')),
                ('status', models.CharField(choices=[('checking_out', 'Checking out'), ('transforming', 'Transforming'), ('documenting', 'Generating docs'), ('succeeded', 'Live'), ('failed', 'Failed')], default='checking_out', max_length=16)),
                ('message', models.TextField(blank=True, default='')),
                ('docs', models.JSONField(blank=True, default=dict, editable=False)),
                ('created_on', models.DateTimeField(auto_now_add=True)),
                ('applied_on', models.DateTimeField(blank=True, editable=False, null=True)),
                ('requested_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'model version',
                'verbose_name_plural': 'model versions',
                'ordering': ('-created_on',),
                'get_latest_by': 'created_on',
            },
        ),
        migrations.RunPython(grant_to_engine, revoke_from_engine),
    ]
