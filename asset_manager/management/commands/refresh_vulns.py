from django.core.management.base import BaseCommand

from asset_manager.vulns import refresh_vulns


class Command(BaseCommand):
    help = "Check the ledger's libraries against OSV.dev and store the known vulnerabilities."

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Run even if VULN_REFRESH_ENABLED is false.',
        )

    def handle(self, *args, **options):
        result = refresh_vulns(force=options['force'])

        if result['skipped']:
            self.stdout.write(self.style.WARNING(
                "Skipped: VULN_REFRESH_ENABLED=false (use --force to override)."
            ))
            return
        if not result['ok']:
            self.stdout.write(self.style.ERROR("Failed: could not reach OSV.dev or NVD."))
            return
        self.stdout.write(self.style.SUCCESS(
            f"Checked {result['packages']} libraries: {result['vulnerable']} with known vulnerabilities"
            + (f", {result['failed']} failed" if result['failed'] else "")
            + f". Checked {result['engines']} database engine versions (RDS / ElastiCache): {result['engines_vulnerable']} with known vulnerabilities"
            + (f", {result['engines_failed']} failed" if result['engines_failed'] else "")
            + "."
        ))
