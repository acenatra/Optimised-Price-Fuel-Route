from django.db import models


class Place(models.Model):
    """A US city/town/CDP/township centroid from the US Census Gazetteer.

    Used to geocode fuel stations (the CSV has no coordinates) and to resolve
    "City, ST" inputs locally without calling an external geocoder.
    """

    state = models.CharField(max_length=2)
    key = models.CharField(max_length=128, help_text="Normalised name used for lookups")
    name = models.CharField(max_length=128)
    lat = models.FloatField()
    lon = models.FloatField()
    weight = models.FloatField(default=0, help_text="Population, used to rank autocomplete results")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["state", "key"], name="uniq_place_state_key")]
        indexes = [models.Index(fields=["key"], name="place_key_idx")]  # serves prefix (LIKE 'abc%') search

    def __str__(self):
        return f"{self.name}, {self.state}"


class FuelStation(models.Model):
    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=128)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    # Best (lowest) retail price reported for this station in the dataset.
    price = models.DecimalField(max_digits=8, decimal_places=5)
    lat = models.FloatField()
    lon = models.FloatField()
    geocode_source = models.CharField(max_length=16, default="gazetteer")

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
