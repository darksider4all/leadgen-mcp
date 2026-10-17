# Niche → CAEN codes, and the 42 counties

Codes are CAEN Rev.2, the classification most firms registered under. Leadgen adds the Rev.3
equivalents automatically and names them in `codesAdded`. A few numbers mean a different trade in
Rev.3 (4391: roofing → masonry; 4791: online retail → retail intermediation; 4673: building
materials → motorcycles). Write those with the `r2` suffix, as below, so the meaning stays the
Rev.2 one; a bare ambiguous code gets a warning naming both meanings. When unsure, pass the user's words as
`topic` and read `matched` / `caenCodes`, or offer the `candidates` of an `ambiguous_topic` answer.

| Niche (RO / EN) | Codes |
|---|---|
| Instalații electrice / electricians | 4321 |
| Instalații sanitare, termice, climatizare / plumbing, heating, HVAC | 4322 |
| Construcții clădiri / building construction | 4120 (4110 = property development) |
| Finisaje: tâmplărie, pardoseli, zugrăveli, acoperișuri / finishing trades | 4332, 4333, 4334, 4391r2 (roofing) |
| Software, programare / software development | 6201 (6202 = IT consultancy, 6311 = hosting) |
| Agenții de publicitate, marketing / advertising agencies | 7311 (7312 media sales, 7320 market research) |
| Contabilitate, audit, consultanță fiscală / accounting | 6920 |
| Avocatură / law firms | 6910 |
| Consultanță în afaceri / management consulting | 7022 |
| Arhitectură, inginerie / architecture, engineering | 7111, 7112 |
| Stomatologie / dentists | 8623 |
| Clinici, medici specialiști / medical practices | 8621, 8622 |
| Veterinari / veterinary | 7500 |
| Restaurante / restaurants | 5610 (5630 bars, 5621 catering) |
| Hoteluri, pensiuni / hotels, guesthouses | 5510, 5520 |
| Transport rutier de mărfuri / road freight | 4941 (5229 forwarding, 5210 warehousing) |
| Service auto / car repair | 4520 (4511 car dealers) |
| Saloane de înfrumusețare / hair and beauty | 9602 |
| Săli de fitness / gyms | 9313 |
| Agenții imobiliare / real-estate agencies | 6831 (6820 renting own property) |
| Școli de șoferi / driving schools | 8553 (8559 other training) |
| Curățenie / cleaning services | 8121 (8130 landscaping) |
| Pază și protecție / security services | 8010 |
| Comerț online / e-commerce | 4791r2 |
| Magazine alimentare / food retail | 4711 |
| Panificație / bakeries | 1071 |
| Materiale de construcții (en-gros) / building materials wholesale | 4673r2, 4674 |

Curated topics (pass the id as `topic`): agriculture, artificial-intelligence, climate,
construction, cybersecurity, e-commerce, education, energy, finance, healthcare, hospitality,
it-services, manufacturing, marketing, real-estate, retail, software, transport.

## Counties (`county`, diacritics optional)

Alba, Arad, Argeș, Bacău, Bihor, Bistrița-Năsăud, Botoșani, Brașov, Brăila, București, Buzău,
Caraș-Severin, Călărași, Cluj, Constanța, Covasna, Dâmbovița, Dolj, Galați, Giurgiu, Gorj,
Harghita, Hunedoara, Ialomița, Iași, Ilfov, Maramureș, Mehedinți, Mureș, Neamț, Olt, Prahova,
Satu Mare, Sălaj, Sibiu, Suceava, Teleorman, Timiș, Tulcea, Vaslui, Vâlcea, Vrancea.
