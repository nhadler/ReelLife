"""Tracks the Credits list and the project lists in history.

Parties, default shares (project and series) and the status, sub status,
theme, instrument and department lists get history, so editing them is
one undoable change set. Each existing row gets a "+" snapshot, so the
first undoable edit has a "before" to go back to. Change sets about the
Credits list belong to no project.
"""

import django.core.validators
import django.db.models.deletion
import simple_history.models
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


NEWLY_TRACKED = (
    "Party",
    "DefaultShare",
    "SeriesShare",
    "StatusOption",
    "SubStatusOption",
    "ThemeOption",
    "InstrumentOption",
    "DepartmentStatus",
)
REASON = "Snapshot when history began"


def snapshot(apps, schema_editor):
    """Writes a "+" history row for every existing row."""
    del schema_editor
    now = timezone.now()
    for name in NEWLY_TRACKED:
        model = apps.get_model("cues", name)
        historical = apps.get_model("cues", f"Historical{name}")
        names = {f.attname for f in historical._meta.concrete_fields}  # noqa: SLF001
        historical.objects.bulk_create(
            historical(
                **{
                    f.attname: getattr(obj, f.attname)
                    for f in model._meta.concrete_fields  # noqa: SLF001
                    if f.attname in names
                },
                history_date=now,
                history_type="+",
                history_change_reason=REASON,
            )
            for obj in model.objects.all()
        )


def drop_global_change_sets(apps, schema_editor):
    """Going back: change sets without a project can't stay."""
    del schema_editor
    apps.get_model("cues", "ChangeSet").objects.filter(project=None).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("cues", "0038_day_off_per_project"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="departmentstatus",
            options={
                "ordering": ["field", "order", "name"],
                "verbose_name": "department status",
                "verbose_name_plural": "department statuses",
            },
        ),
        migrations.AlterModelOptions(
            name="instrumentoption",
            options={
                "ordering": ["order", "name"],
                "verbose_name": "instrument",
            },
        ),
        migrations.AlterModelOptions(
            name="party",
            options={
                "ordering": ["kind", "name"],
                "verbose_name": "writer or publisher",
                "verbose_name_plural": "writers and publishers",
            },
        ),
        migrations.AlterModelOptions(
            name="statusoption",
            options={
                "ordering": ["order", "id"],
                "verbose_name": "status",
                "verbose_name_plural": "statuses",
            },
        ),
        migrations.AlterModelOptions(
            name="substatusoption",
            options={
                "ordering": ["order", "label"],
                "verbose_name": "sub status",
                "verbose_name_plural": "sub statuses",
            },
        ),
        migrations.AlterModelOptions(
            name="themeoption",
            options={"ordering": ["order", "name"], "verbose_name": "theme"},
        ),
        migrations.AlterField(
            model_name="changeset",
            name="project",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="change_sets",
                to="cues.project",
            ),
        ),
        migrations.CreateModel(
            name="HistoricalDefaultShare",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True,
                        blank=True,
                        db_index=True,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "updated_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "change_seq",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("C", "Composer"),
                            ("A", "Author/Lyricist"),
                            ("CA", "Composer/Author"),
                            ("AR", "Arranger"),
                            ("AD", "Adaptor"),
                            ("E", "Original Publisher"),
                            ("SE", "Sub-Publisher"),
                            ("AM", "Administrator"),
                        ],
                        max_length=2,
                    ),
                ),
                (
                    "share",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(
                                Decimal("0.01")
                            ),
                            django.core.validators.MaxValueValidator(
                                Decimal("100")
                            ),
                        ],
                        verbose_name="share %",
                    ),
                ),
                ("order", models.PositiveSmallIntegerField(default=0)),
                (
                    "history_id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
                ("history_date", models.DateTimeField(db_index=True)),
                (
                    "history_change_reason",
                    models.CharField(max_length=100, null=True),
                ),
                (
                    "history_type",
                    models.CharField(
                        choices=[
                            ("+", "Created"),
                            ("~", "Changed"),
                            ("-", "Deleted"),
                        ],
                        max_length=1,
                    ),
                ),
                (
                    "change_set",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="cues.changeset",
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "party",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="cues.party",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="cues.project",
                    ),
                ),
            ],
            options={
                "verbose_name": "historical default share",
                "verbose_name_plural": "historical default shares",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="HistoricalDepartmentStatus",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True,
                        blank=True,
                        db_index=True,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "updated_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "change_seq",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                (
                    "color",
                    models.CharField(
                        max_length=7,
                        validators=[
                            django.core.validators.RegexValidator(
                                "^#[0-9a-fA-F]{6}$",
                                "Enter a colour like #1a2b3c.",
                            )
                        ],
                    ),
                ),
                ("order", models.PositiveIntegerField(default=0)),
                (
                    "field",
                    models.CharField(
                        choices=[
                            ("orchestration_status", "Orchestration Status"),
                            ("recording_status", "Recording Status"),
                            ("mix_status", "Mix Status"),
                            ("music_edit_status", "Music Edit Status"),
                            ("licensing_status", "Licensing Status"),
                        ],
                        max_length=20,
                    ),
                ),
                ("name", models.CharField(max_length=30, verbose_name="label")),
                (
                    "history_id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
                ("history_date", models.DateTimeField(db_index=True)),
                (
                    "history_change_reason",
                    models.CharField(max_length=100, null=True),
                ),
                (
                    "history_type",
                    models.CharField(
                        choices=[
                            ("+", "Created"),
                            ("~", "Changed"),
                            ("-", "Deleted"),
                        ],
                        max_length=1,
                    ),
                ),
                (
                    "change_set",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="cues.changeset",
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="cues.project",
                    ),
                ),
            ],
            options={
                "verbose_name": "historical department status",
                "verbose_name_plural": "historical department statuses",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="HistoricalInstrumentOption",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True,
                        blank=True,
                        db_index=True,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "updated_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "change_seq",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("name", models.CharField(max_length=50)),
                (
                    "color",
                    models.CharField(
                        max_length=7,
                        validators=[
                            django.core.validators.RegexValidator(
                                "^#[0-9a-fA-F]{6}$",
                                "Enter a colour like #1a2b3c.",
                            )
                        ],
                    ),
                ),
                ("order", models.PositiveIntegerField(default=0)),
                (
                    "history_id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
                ("history_date", models.DateTimeField(db_index=True)),
                (
                    "history_change_reason",
                    models.CharField(max_length=100, null=True),
                ),
                (
                    "history_type",
                    models.CharField(
                        choices=[
                            ("+", "Created"),
                            ("~", "Changed"),
                            ("-", "Deleted"),
                        ],
                        max_length=1,
                    ),
                ),
                (
                    "change_set",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="cues.changeset",
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="cues.project",
                    ),
                ),
            ],
            options={
                "verbose_name": "historical instrument",
                "verbose_name_plural": "historical instruments",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="HistoricalParty",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True,
                        blank=True,
                        db_index=True,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "updated_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "change_seq",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("name", models.CharField(max_length=200)),
                (
                    "first_name",
                    models.CharField(
                        blank=True,
                        help_text="And middle names.",
                        max_length=100,
                        verbose_name="first name",
                    ),
                ),
                (
                    "last_name",
                    models.CharField(
                        blank=True, max_length=100, verbose_name="last name"
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("writer", "Writer"),
                            ("publisher", "Publisher"),
                        ],
                        max_length=10,
                    ),
                ),
                (
                    "society",
                    models.CharField(
                        blank=True,
                        help_text="ASCAP, BMI, SESAC, GMR, SOCAN, PRS …",
                        max_length=30,
                        verbose_name="PRO",
                    ),
                ),
                (
                    "ipi",
                    models.CharField(
                        blank=True,
                        max_length=11,
                        validators=[
                            django.core.validators.RegexValidator(
                                "^\\d{9,11}$",
                                "An IPI/CAE number is 9 to 11 digits.",
                            )
                        ],
                        verbose_name="IPI/CAE #",
                    ),
                ),
                (
                    "is_mine",
                    models.BooleanField(
                        default=False,
                        help_text="One of the composer's own writer or publishing accounts.",
                        verbose_name="the composer's own",
                    ),
                ),
                (
                    "history_id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
                ("history_date", models.DateTimeField(db_index=True)),
                (
                    "history_change_reason",
                    models.CharField(max_length=100, null=True),
                ),
                (
                    "history_type",
                    models.CharField(
                        choices=[
                            ("+", "Created"),
                            ("~", "Changed"),
                            ("-", "Deleted"),
                        ],
                        max_length=1,
                    ),
                ),
                (
                    "change_set",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="cues.changeset",
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "historical writer or publisher",
                "verbose_name_plural": "historical writers and publishers",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="HistoricalSeriesShare",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True,
                        blank=True,
                        db_index=True,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "updated_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "change_seq",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("C", "Composer"),
                            ("A", "Author/Lyricist"),
                            ("CA", "Composer/Author"),
                            ("AR", "Arranger"),
                            ("AD", "Adaptor"),
                            ("E", "Original Publisher"),
                            ("SE", "Sub-Publisher"),
                            ("AM", "Administrator"),
                        ],
                        max_length=2,
                    ),
                ),
                (
                    "share",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(
                                Decimal("0.01")
                            ),
                            django.core.validators.MaxValueValidator(
                                Decimal("100")
                            ),
                        ],
                        verbose_name="share %",
                    ),
                ),
                ("order", models.PositiveSmallIntegerField(default=0)),
                (
                    "history_id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
                ("history_date", models.DateTimeField(db_index=True)),
                (
                    "history_change_reason",
                    models.CharField(max_length=100, null=True),
                ),
                (
                    "history_type",
                    models.CharField(
                        choices=[
                            ("+", "Created"),
                            ("~", "Changed"),
                            ("-", "Deleted"),
                        ],
                        max_length=1,
                    ),
                ),
                (
                    "change_set",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="cues.changeset",
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "party",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="cues.party",
                    ),
                ),
                (
                    "series",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="cues.series",
                    ),
                ),
            ],
            options={
                "verbose_name": "historical series share",
                "verbose_name_plural": "historical series shares",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="HistoricalStatusOption",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True,
                        blank=True,
                        db_index=True,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "updated_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "change_seq",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("label", models.CharField(max_length=50)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("to_do", "To do"),
                            ("in_progress", "In progress"),
                            ("redo", "Redo"),
                            ("changes", "Changes"),
                            ("done", "Done"),
                            ("approved", "Approved"),
                            ("completed", "Completed"),
                            ("on_hold", "On hold"),
                            ("omit", "Omit"),
                            ("song", "Song"),
                            ("license", "License"),
                        ],
                        max_length=15,
                    ),
                ),
                (
                    "color",
                    models.CharField(
                        max_length=7,
                        validators=[
                            django.core.validators.RegexValidator(
                                "^#[0-9a-fA-F]{6}$",
                                "Enter a colour like #1a2b3c.",
                            )
                        ],
                    ),
                ),
                ("order", models.PositiveIntegerField(default=0)),
                (
                    "history_id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
                ("history_date", models.DateTimeField(db_index=True)),
                (
                    "history_change_reason",
                    models.CharField(max_length=100, null=True),
                ),
                (
                    "history_type",
                    models.CharField(
                        choices=[
                            ("+", "Created"),
                            ("~", "Changed"),
                            ("-", "Deleted"),
                        ],
                        max_length=1,
                    ),
                ),
                (
                    "change_set",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="cues.changeset",
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="cues.project",
                    ),
                ),
            ],
            options={
                "verbose_name": "historical status",
                "verbose_name_plural": "historical statuses",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="HistoricalSubStatusOption",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True,
                        blank=True,
                        db_index=True,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "updated_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "change_seq",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("label", models.CharField(max_length=50)),
                (
                    "color",
                    models.CharField(
                        max_length=7,
                        validators=[
                            django.core.validators.RegexValidator(
                                "^#[0-9a-fA-F]{6}$",
                                "Enter a colour like #1a2b3c.",
                            )
                        ],
                    ),
                ),
                ("order", models.PositiveIntegerField(default=0)),
                (
                    "history_id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
                ("history_date", models.DateTimeField(db_index=True)),
                (
                    "history_change_reason",
                    models.CharField(max_length=100, null=True),
                ),
                (
                    "history_type",
                    models.CharField(
                        choices=[
                            ("+", "Created"),
                            ("~", "Changed"),
                            ("-", "Deleted"),
                        ],
                        max_length=1,
                    ),
                ),
                (
                    "change_set",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="cues.changeset",
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="cues.project",
                    ),
                ),
            ],
            options={
                "verbose_name": "historical sub status",
                "verbose_name_plural": "historical sub statuses",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="HistoricalThemeOption",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True,
                        blank=True,
                        db_index=True,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "updated_at",
                    models.DateTimeField(blank=True, editable=False),
                ),
                (
                    "change_seq",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("name", models.CharField(max_length=50)),
                (
                    "color",
                    models.CharField(
                        max_length=7,
                        validators=[
                            django.core.validators.RegexValidator(
                                "^#[0-9a-fA-F]{6}$",
                                "Enter a colour like #1a2b3c.",
                            )
                        ],
                    ),
                ),
                ("order", models.PositiveIntegerField(default=0)),
                (
                    "history_id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
                ("history_date", models.DateTimeField(db_index=True)),
                (
                    "history_change_reason",
                    models.CharField(max_length=100, null=True),
                ),
                (
                    "history_type",
                    models.CharField(
                        choices=[
                            ("+", "Created"),
                            ("~", "Changed"),
                            ("-", "Deleted"),
                        ],
                        max_length=1,
                    ),
                ),
                (
                    "change_set",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="cues.changeset",
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="cues.project",
                    ),
                ),
            ],
            options={
                "verbose_name": "historical theme",
                "verbose_name_plural": "historical themes",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.RunPython(snapshot, migrations.RunPython.noop),
        migrations.RunPython(
            migrations.RunPython.noop, drop_global_change_sets
        ),
    ]
