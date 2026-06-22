from django.conf import settings
from django.core.management.base import BaseCommand

from asset_manager.models import DriftSnapshot, Environment


class Command(BaseCommand):
    help = (
        "Prune old DriftSnapshot rows, keeping the newest N per environment. "
        "Use this to clean up tables that grew before retention was enforced."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--keep', type=int, default=None,
            help='Rows to keep per environment (default: settings.DRIFT_SNAPSHOT_RETENTION).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be deleted without deleting.',
        )

    def handle(self, *args, **options):
        keep = options['keep']
        if keep is None:
            keep = getattr(settings, 'DRIFT_SNAPSHOT_RETENTION', 0)

        if not keep or keep <= 0:
            self.stdout.write(self.style.WARNING(
                f"Retention is unlimited (keep={keep}); nothing to prune. "
                "Set --keep or DRIFT_SNAPSHOT_RETENTION."
            ))
            return

        total_deleted = 0
        for env in Environment.objects.all():
            qs = DriftSnapshot.objects.filter(environment=env)
            count = qs.count()
            if count <= keep:
                continue

            if options['dry_run']:
                would = count - keep
                total_deleted += would
                self.stdout.write(f"{env}: would delete {would} (of {count})")
                continue

            deleted = DriftSnapshot.prune(env, keep=keep)
            total_deleted += deleted
            self.stdout.write(f"{env}: deleted {deleted} (kept {keep})")

        verb = "would delete" if options['dry_run'] else "deleted"
        self.stdout.write(self.style.SUCCESS(
            f"Done: {verb} {total_deleted} snapshot(s), keeping {keep} per environment."
        ))
