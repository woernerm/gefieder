"""Drop the editable slug from Query, Chart and Panel; rename the dashboard's to a name.

An editable identifier is the worst of both worlds: editing it silently breaks every link
and template embed that used it, and it takes up room on a form where nobody wants to
think about it. The primary key is the one thing nobody wants to change, so it is what a
panel is now embedded and fetched by.

The dashboard keeps a short name, because it is the one model looked up by something a
person wrote rather than by a link: the admin index carries the dashboard called "home",
and a template embeds a dashboard by naming it.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0002_split_query_chart_panel_dashboard"),
    ]

    operations = [
        migrations.RenameField(
            model_name="dashboard", old_name="slug", new_name="name",
        ),
        migrations.AlterField(
            model_name="dashboard",
            name="name",
            field=models.SlugField(
                help_text='Identifier for this dashboard. The one called "home" is the admin index.',
                max_length=100,
                unique=True,
                verbose_name="name",
            ),
        ),
        migrations.RemoveField(model_name="query", name="slug"),
        migrations.RemoveField(model_name="chart", name="slug"),
        migrations.RemoveField(model_name="panel", name="slug"),
    ]
