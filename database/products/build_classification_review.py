"""One-off, READ-ONLY review list for the catalog classified from catalogo_clasificado.xlsx
(2026-10-03). Never writes to the database. Output: classification_review_candidates.csv

How the list was built (audit of the products currently classified):
  1. name signals per category (dog/cat/litter/brand words, liquor types/brands, cigarette brands);
  2. families: GS1 company prefix of the barcode (first 7 digits) whose products are mostly in
     MASCOTAS_CON_CODIGO / LICORES / CIGARROS, plus first-word families (AGILITY, ARENA, CORONITA…);
  3. every hit reviewed by hand; false positives excluded explicitly (EXCLUDED below).
Names only DETECT candidates. Any later correction is applied by barcode from an approved list.

Usage (host, needs the products export and the applied-import CSV):
  python build_classification_review.py <products.tsv> classification_applied_20261003.csv out.csv
"""
import csv
import sys

M, L, C = "MASCOTAS_CON_CODIGO", "LICORES", "CIGARROS"

# (barcodes, suggested, confidence, family_or_brand, reason)
RULES = [
    # ---------------- SUPER -> MASCOTAS_CON_CODIGO
    (["7707205154455", "7707205150280", "7707205157753", "7707205157685", "7707205154530", "7707205150730",
      "7707205151232", "7707205154851", "7707205158194", "7707205150266", "7707205154233", "7707205154240",
      "7707205157715", "7707205157746"], M, "HIGH", "AGILITY",
     "Alimento para perro/gato (cachorros, gatitos, adultos); marca de mascotas, prefijo 7707205"),
    (["7707205151539"], M, "HIGH", "DELICAT", "Alimento húmedo para gato; misma empresa que AGILITY (7707205), DELICAT ya en mascotas"),
    (["7707205156404"], M, "HIGH", "SIETE VIDAS", "Alimento para gato 1.3 kg; prefijo 7707205 (AGILITY/DELIDOG)"),
    (["6932284300559", "6972228798370", "6932284304861", "6932284306988", "6972228797526", "6972228791654",
      "6932284304885"], M, "HIGH", "ARENA CALABAZA", "Arena sanitaria para gato; la misma línea ARENA CALABAZA ya está en mascotas"),
    (["7708949397658", "7708949397610", "7709781648991"], M, "HIGH", "ARENA MICHIKO TOFU", "Arena de tofu para gato (marca MICHIKO)"),
    (["7709781648960", "8855134011406", "6972229785379", "8858711944586", "6972229785362"], M, "HIGH", "ARENA TOFU",
     "Arena de tofu / 'litter' para gato"),
    (["6973689220080"], M, "HIGH", "ARENA MEOW PLANET", "Arena para gato; prefijo 6973689 mayoritariamente mascotas"),
    (["7707196563618", "7707196563601"], M, "HIGH", "FREE MIAU", "Arena para gato 4–4.5 kg; prefijo 7707196 mayoritariamente mascotas"),
    (["6972228791616"], M, "HIGH", "PALA ARENA", "Pala para arena de gato; 'PALA ARENERA' ya está en mascotas"),
    (["7708574195193", "7708574195780", "7709387191716", "7708574195032", "7709448928978", "7709448928985",
      "7708574195674", "7709387191785", "7709448928909"], M, "HIGH", "MICHIKO",
     "Snack/alimento para gato MICHIKO (incl. 'NICHIKO' = errata); prefijos 7708574/7709448 de la misma marca en mascotas"),
    (["7709161452446", "857848093578", "857848000767", "857848093530", "857848039682", "857848000590",
      "7896588950072", "7896588950058", "857848000613", "7709249794369", "857848123817", "857848093608",
      "857848126672", "7896588958375", "7896588958436", "7896588958610", "7896588958566"], M, "HIGH", "MAX (alimento mascota)",
     "Concentrado MAX (A.R.P, cachorros, gatito, mature, vita, light) 1–22 kg; prefijos 857848/7896588 de alimento para mascotas. NO incluye dulces/aseo MAX"),
    (["706460000665", "706460241662", "7896029046623"], M, "HIGH", "WHISKAS", "WHISKAS escrito 'WHIKAS'/'WISKAS'; prefijos 0706460/7896029 de WHISKAS/PEDIGREE en mascotas"),
    (["7501072213839", "7501072213921", "7501072213860", "7501072213853"], M, "HIGH", "FANCY FEAST", "Alimento húmedo para gato (Purina); prefijo 7501072 mayoritariamente mascotas"),
    (["7501072214515"], M, "HIGH", "PRO PLAN", "PROPLAN (Purina) 85 g"),
    (["7506174515096", "7506174515102", "7506174515089"], M, "HIGH", "PEDIGREE", "'PED POUCHE' = Pedigree pouch 85 g; prefijo 7506174 de PEDIGREE en mascotas"),
    (["7891000332429", "7891000332467", "7891000332306", "7891000332382"], M, "HIGH", "PURINA ONE", "Purina ONE pouch 85 g para gato"),
    (["7702521880057"], M, "HIGH", "GATSY", "Alimento para gatitos; prefijo 7702521 (18 productos en mascotas, solo este en SUPER)"),
    (["7703090552055"], M, "HIGH", "MININO", "Alimento para gato; prefijo 7703090 (MIRRINGO, 11 en mascotas)"),
    (["052742623801", "052742945309"], M, "HIGH", "HILL'S", "Hill's Prescription Diet c/d urinary y k/d renal (latas veterinarias)"),
    (["3428460084014"], M, "HIGH", "PLAISIR", "Paté urinary para gato"),
    (["7797453971836"], M, "HIGH", "DENTASTIX", "Snack dental para perro (Pedigree)"),
    (["7702084057194", "7702084057187", "7702084057200"], M, "HIGH", "OH MAI GAT!", "Alimento para gato adulto; prefijo 7702084 (ARENA UNI CAT en mascotas)"),
    (["7703381240043", "7703381240050"], M, "HIGH", "NEXGARD", "Antipulgas/garrapatas para perro por peso; prefijo 7703381 con productos en mascotas"),
    (["7702487013148"], M, "HIGH", "PETYS", "Tapetes para cachorro PETYS; prefijo 7702487 (CANAMOR) mayoritariamente mascotas"),
    (["7707309252880"], M, "HIGH", "ALIMENTO HAMSTER", "Alimento para hámster"),
    (["6956131004513", "6956131004278"], M, "MEDIUM", "ARENA ECOLOGICA", "Arena ecológica de pino 6 L (probable arena para gato)"),
    (["7703220046515"], M, "MEDIUM", "ARENA FURRY", "Probable arena para gato"),
    (["7709355546449"], M, "MEDIUM", "ARENA DE MAIZ", "Probable arena de maíz para gato"),
    (["6972228797496"], M, "MEDIUM", "BOLSAS RECOGER", "Repuesto de bolsas para recoger (heces de mascota); prefijo 6972228 = ARENA CALABAZA"),
    (["7702487193314"], M, "MEDIUM", "PETYS", "PETYS clorhexidina (pañitos para mascota)"),
    (["7891000042151", "7891000042052", "7891000071700"], M, "MEDIUM", "PURINA (abreviado)",
     "'GTA POLLO A.R.P/A.R.M.G 500 G' y 'FILHOTES' (= cachorros): probable Dog Chow/Purina; nombre abreviado"),
    (["7891000419151", "7891000419199"], M, "MEDIUM", "TRAVESURAS", "Probable Felix Travesuras (snack para gato, Purina)"),
    (["050000290635", "050000290680"], M, "MEDIUM", "FELIX (lata 156 g)", "Lata 156 g con prefijo 0050000 = línea FELIX en mascotas; el nombre no lo dice"),
    (["5420036974499", "5420036954644"], M, "MEDIUM", "ASUNTOL", "Jabón ASUNTOL (antiparasitario veterinario); 'JABON CANINO ASUNTOL' ya en mascotas; prefijo Elanco"),
    (["7702123000945"], M, "MEDIUM", "NEGUVON", "Antiparasitario veterinario en polvo"),
    (["7709990844429"], M, "MEDIUM", "HUESO", "Hueso de hígado 65 g (snack para perro); prefijo 7709990 (FIPRODOG en mascotas)"),
    (["7702084057521", "7702084057552", "7702084057491"], M, "MEDIUM", "SNACK CREMOSO", "Snack cremoso 12 g atún/pollo/salmón; prefijo de OH MAI GAT! / ARENA UNI CAT"),
    (["7707001120104"], M, "MEDIUM", "NUTRION", "Probable alimento para perro adulto 400 g"),
    (["7896048915597"], M, "MEDIUM", "NATURAL", "'NATURAL GSTOS ADULTOS 1.5 KG' = probable alimento para gatos"),
    (["7502281124985"], M, "MEDIUM", "GANADOR", "Probable alimento para perro adulto 500 g"),
    (["7702712004873"], M, "MEDIUM", "FILPO", "Bulto de concentrado FILPO (con código); prefijo 7702712 mayoritariamente mascotas"),
    (["7702993059531", "7702993059524", "7702993059548"], M, "LOW", "MUAU",
     "Snack 14 g atún/pollo/salmón (posible snack para gato); el prefijo es de una dulcería — dudoso"),
    (["7707912072646"], M, "LOW", "RONIK", "'RONIK CORDERO 500 G' — posible alimento para mascota, sin más evidencia"),
    # ---------------- SUPER -> LICORES
    (["7709997134813", "7702049001675"], L, "HIGH", "ANTIOQUEÑO", "Aguardiente Antioqueño; prefijo 7702049 de licorera"),
    (["7610113006184"], L, "HIGH", "BACARDI", "Ron Bacardi"),
    (["849806005303", "849806002319", "849806001220", "849806005334", "849806001756", "849806005273",
      "849806002746", "849806005365", "849806001206"], L, "HIGH", "FOUR LOKO", "Bebida alcohólica Four Loko"),
    (["7804320148397", "7804320116815", "7804300010638", "7804300010034", "7804350003222", "7804330006731",
      "7804414005971", "7804315010913"], L, "HIGH", "VINOS CHILENOS",
     "Vino 750 ml (Concha y Toro, Gato Negro, Santa Helena, Santa Carolina, Santa Rita, Terra Vega, Lazo); mismos prefijos en LICORES"),
    (["7702004005724", "7702004005717", "7707430876641", "7702004010681", "7702004009944", "7702004110725",
      "7702004110701", "7702004110749", "7702004110565", "7702004006813", "7707430875408"], L, "HIGH", "CERVEZA",
     "Cerveza (Costeña, Andina, Michelob, Beck's, Coronita, Budweiser 'DUDWEUSER', Club Colombia 'CLIB', 3 Cordilleras); prefijos de cervecería con mayoría en LICORES"),
    (["7702004006899", "7702004004253", "7702004035103", "7702004035110"], L, "MEDIUM", "CERVEZA (dudosa)",
     "Club Colombia café / Club Trigo / Kaufmann: probable cerveza de Bavaria; confirmar que no sea versión sin alcohol"),
    (["7702004194206", "7702004016652", "7702004016614", "7702004016621"], L, "MEDIUM", "REFAJO",
     "Refajo Cola & Pola (cerveza + gaseosa, con alcohol); 'REFAJO' ya está en LICORES"),
    (["7702356211217", "7703588003137", "7705518662117", "7702121001333"], L, "MEDIUM", "ESPUMOSO/VINO",
     "Champaña/espumoso/moscatel/semi seco 750–1000 ml; prefijos de licorera; confirmar contenido alcohólico"),
    (["7702168893885", "7702168439847", "7702168866490"], L, "MEDIUM", "COCTEL R.V.C", "Coctel listo con Ron Viejo de Caldas (mojito, daiquiri, cuba libre); prefijo 7702168 en LICORES"),
    (["7702356611017", "7703518013656"], L, "MEDIUM", "LICOR", "Piña colada Antillana / Copacabana 700–750 ml; prefijos de licorera"),
    (["7705518004719"], L, "LOW", "PIÑA COLADA", "Piña colada Capri 750 ml: puede ser mezcla sin alcohol"),
    # ---------------- SUPER -> CIGARROS
    (["7702005602021"], C, "HIGH", "MARLBORO", "'MLB RED SELECTION X 10 UNDS' = Marlboro; prefijo 7702005 (18 en CIGARROS)"),
    (["7702303963527"], C, "HIGH", "ROTHMANS", "'ROTHMAN BOMBAY X 10 UNDS'; prefijo 7702303 de cigarrillos"),
    (["7702303953863", "7702303809221", "7702303778886", "7702303006057", "7702303842341", "7702303611862"], C, "HIGH", "LUCKY STRIKE",
     "LUCKY Alaska/Sandía/Morado/Azul x10/x20 = Lucky Strike (CIGARROS ya tiene 'LUKY SANDIA + MENTOL'); prefijo 7702303 de cigarrillos"),
]

# Reviewed and deliberately NOT candidates (documented, never reclassified by weak words)
EXCLUDED = ("ADVIL MAX, SPEED MAX, MAX dulces/aseo (7702011, VANISH, EGO, ENERGIZER, SCOTT), PAN PERRO/SALCH PERRO/ZENU PERRO, "
            "KIT KAT, EQUILIBRIO (brownie/muffin), MARGATITA, CAFE AGUILA ROJA, CHOCOLATE/PANCAKES CORONA, CLUB SOCIAL, "
            "NECTAR (jugos), PONY/NATU MALTA, HIDRATAO, cervezas 0.0/CERO, BAILEYS bombones, ponqué de vino, ron con pasas, "
            "SNACK CERVEC, ENCENDEDORES, AZUCAR MAYAGUEZ, ALFAJOR, LECHERA")


def main(products_tsv: str, applied_csv: str, out_csv: str) -> None:
    products = {}
    with open(products_tsv, encoding="utf-8") as handle:
        for row in list(csv.reader(handle, delimiter="\t"))[1:]:
            pid, barcode, name, active, category = row
            if barcode:
                products[barcode] = (pid, name, active, category)
    excel_row = {}
    with open(applied_csv, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            excel_row[row["barcode"]] = row["excel_fila"]
    seen, out = set(), []
    for barcodes, suggested, confidence, family, reason in RULES:
        for barcode in barcodes:
            assert barcode not in seen, f"duplicated candidate {barcode}"
            seen.add(barcode)
            pid, name, active, current = products[barcode]          # KeyError = barcode not in POS
            assert current != suggested, f"{barcode} already {suggested}"
            out.append({"barcode": barcode, "product_name": name, "current_category": current,
                        "suggested_category": suggested, "confidence": confidence, "reason": reason,
                        "family_or_brand": family, "source_excel_row": excel_row.get(barcode, ""),
                        "active": active, "product_id": pid})
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(out[0]), delimiter=";")
        writer.writeheader(); writer.writerows(out)
    print(f"written {len(out)} candidates -> {out_csv}\nexcluded (reviewed): {EXCLUDED}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
