from django.core.management.base import BaseCommand

from asset_manager.repo_import import refresh_repos


class Command(BaseCommand):
    help = "Re-import the lockfiles of every environment that has a GitHub repository registered."

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Run even if REPO_REFRESH_ENABLED is false.')

    def handle(self, *args, **options):
        result = refresh_repos(force=options['force'])
        if result['skipped']:
            self.stdout.write(self.style.WARNING("Skipped: REPO_REFRESH_ENABLED=false (use --force to override)."))
            return
        self.stdout.write(self.style.SUCCESS(
            f"Synced {result['synced']} environment(s)"
            + (f", {result['failed']} failed (see the Applications screen)" if result['failed'] else "")
            + "."
        ))
