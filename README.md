# Nástroj na extrakciu textu z PDF dokumentov - bakalárska práca

Tento projekt predstavuje návrh a implementáciu webovej aplikácie na extrakciu, čistenie a základné spracovanie textu z PDF dokumentov. Aplikácia je vytvorená v jazyku Python s využitím frameworku Flask a slúži na spracovanie jedného alebo viacerých PDF súborov naraz.

## Popis projektu

Systém umožňuje používateľovi nahrať PDF dokumenty cez webové rozhranie a následne z nich získať textový obsah. Na extrakciu textu sa využíva knižnica `pdfplumber`. Ak dokument neobsahuje dostatočnú textovú vrstvu alebo ide o naskenovaný dokument, aplikácia môže použiť OCR rozpoznávanie pomocou nástroja `Tesseract`.

Po získaní textu systém vykonáva jeho základné čistenie, napríklad:

- odstránenie nadbytočných medzier,
- úpravu riadkov,
- normalizáciu odrážok,
- odstránenie opakujúcich sa hlavičiek a pätičiek.

Súčasťou spracovania je aj jednoduchá detekcia nadpisov a základná klasifikácia obsahu stránky, napríklad na text, obrázok, graf alebo tabuľku.

Výsledné údaje je možné zobraziť priamo vo webovej aplikácii a exportovať do viacerých formátov.

## Hlavné funkcie

- nahrávanie jedného alebo viacerých PDF súborov
- extrakcia textu z PDF dokumentov
- OCR spracovanie pri skenovaných dokumentoch
- čistenie textu a odstránenie opakovaných hlavičiek a pätičiek
- základná detekcia nadpisov
- klasifikácia obsahu stránok
- export výstupu do TXT, JSON a XML
- zobrazenie výsledkov vo webovom rozhraní
- meranie času spracovania dokumentu

## Použité technológie

- Python
- Flask
- pdfplumber
- pytesseract
- Pillow
- HTML
- CSS
- JavaScript
- Tesseract OCR

## Spôsob fungovania

Používateľ nahrá PDF dokument alebo viac dokumentov cez webové rozhranie. Systém následne spracuje každú stranu dokumentu samostatne. Najskôr sa pokúsi získať text priamo z PDF. Ak je text príliš krátky alebo chýba, môže sa aktivovať OCR.

Po extrakcii sa text vyčistí a upraví. Aplikácia následne zobrazí výsledný text, informácie o stranách, prípadne nájdené nadpisy a základný typ obsahu. Výsledok je možné exportovať do vybraného formátu.

## Obmedzenia systému

- tabuľky a grafické prvky nemusia byť spracované úplne presne
- OCR presnosť závisí od kvality skenu a použitého jazyka
- pri väčšom množstve alebo zložitejších dokumentoch môže byť spracovanie pomalšie

## Možnosti rozšírenia

- rozšírenie podpory ďalších jazykov pre OCR
- doplnenie databázovej vrstvy
- prepojenie s ďalšími nástrojmi na analýzu textu

## Autor

Štefan Krajczár

## Typ práce

Bakalárska práca – Aplikovaná informatika  
Univerzita Konštantína Filozofa v Nitre
