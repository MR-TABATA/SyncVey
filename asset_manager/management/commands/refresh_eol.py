from django.core.management.base import BaseCommand

from asset_manager.eol_refresh import refresh_eol


class Command(BaseCommand):
    help = "Fetch EOL data from endoflife.date and store a snapshot in the DB."

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Run even if EOL_REFRESH_ENABLED is false.',
        )

    def handle(self, *args, **options):
        result = refresh_eol(force=options['force'])

        if result['skipped']:
            self.stdout.write(self.style.WARNING(
                "Skipped: EOL_REFRESH_ENABLED=false (use --force to override)."
            ))
            return
        if not result['ok']:
            self.stdout.write(self.style.ERROR(
                f"Failed: no data fetched ({result['failed']} products failed)."
            ))
            return
        self.stdout.write(self.style.SUCCESS(
            f"EOL snapshot saved: {result['products']} products"
            + (f", {result['failed']} skipped/failed" if result['failed'] else "")
            + "."
        ))
