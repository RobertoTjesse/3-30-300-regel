# 3-30-300-regel: de 3-30-300-regel per woning, buurt, wijk en gemeente

*English and technical documentation: [README.md](README.md) and the [wiki](https://github.com/RobertoTjesse/3-30-300-regel/wiki).*

De 3-30-300-regel (Konijnendijk, 2023) vraagt op drie manieren om groen dicht bij huis: vanuit elke woning 3 bomen zien, 30% boomkronen in elke buurt, en een park binnen 300 m van elke woning. Deze repository berekent alle drie voor de provincie Zuid-Holland, per woning, buurt, wijk en gemeente, en publiceert ze als één webkaart:

**https://robertotjesse.github.io/3-30-300-regel/**

| | Uitkomst Zuid-Holland |
|---|---|
| 3 | 85,9% van de woningen ziet minstens 3 bomen; 5,0% ziet er geen |
| 30 | 1,8% van de woningen heeft minstens 30% kroonbedekking binnen 500 m; gemiddeld is het 12,7%. Wassenaar springt eruit met 27% van de woningen |
| 300 | 36,7% van de woningen ligt binnen 300 m lopen van een park of ander openbaar groen, 56,0% binnen 5 minuten en 97,8% binnen 15 minuten |

Alles draait op QGIS en de onderdelen die QGIS meelevert (GDAL, GRASS, de Python van QGIS); er is geen andere software of licentie nodig. Elke provincie kan het herhalen zonder de code te veranderen.

**Uitleg in het Nederlands** staat op de site, per onderdeel met de keuzes, de aannames en de beperkingen:

- [Overzicht](https://robertotjesse.github.io/3-30-300-regel/uitleg/): de kaart, wat open is, en zelf doen voor een andere provincie
- [De 3](https://robertotjesse.github.io/3-30-300-regel/uitleg/3.html), [de 30](https://robertotjesse.github.io/3-30-300-regel/uitleg/30.html) en [de 300](https://robertotjesse.github.io/3-30-300-regel/uitleg/300.html)
- [Het artikel van Konijnendijk](https://robertotjesse.github.io/3-30-300-regel/uitleg/konijnendijk.html)

De technische documentatie (installatie, gegevens, rekentijden, validatie) is Engelstalig: [README.md](README.md) en de [wiki](https://github.com/RobertoTjesse/3-30-300-regel/wiki). Open punten staan bij de [issues](https://github.com/RobertoTjesse/3-30-300-regel/issues).
