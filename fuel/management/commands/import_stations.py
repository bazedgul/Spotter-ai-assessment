"""Management command stub for importing fuel stations."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Imports fuel stations from CSV, filters non-USA records, deduplicates, and resolves coordinates."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("import_stations command bootstrap ready."))
