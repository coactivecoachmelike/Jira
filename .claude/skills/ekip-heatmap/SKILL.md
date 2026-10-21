---
name: ekip-heatmap
description: "Ürün Ekipleri" Excel dosyasından (Ürünler sayfası; Board Name/Product Grouping, Scrum Master, Product Manager, Yazılımcılar, Analistler, Testerlar kolonları) board/ürün bazlı ekip heatmap'i üretir. Hangi board'da kimin hangi rolde (Yazılımcı, Analist, Testçi, Product Owner, Scrum Master) çalıştığını, kişilerin unique listesini ve her kişinin aynı anda kaç board/ürün ekibinde yer aldığını renklendirerek darboğazları gösterir. Kullanıcı yeni bir "Ürün Ekipleri-<tarih>.xlsx" verip "heatmap", "ekip heatmap'i", "kim kaç board'da", "darboğaz", "takım dağılımı", "güncelini oluştur" dediğinde kullan.
---

# Ekip Heatmap

Kullanıcının elle hazırladığı heatmap çalışmasının otomatik hâli. Yeni bir
"Ürün Ekipleri" dosyası geldiğinde aynı adımlarla güncel heatmap Excel'ini üretir.

## Çalıştırma

```bash
python .claude/skills/ekip-heatmap/scripts/build_heatmap.py \
  --input  "<yeni Ürün Ekipleri dosyası>.xlsx" \
  --previous "<bir önceki heatmap>.xlsx" \
  --output "Ürün Ekipleri Heatmap-<tarih>.xlsx"
```

- `--previous` (önerilir): bir önceki heatmap. Elle düzeltilmiş **roller**
  (ör. TPO, Governance, Yazılım birim yöneticisi) ve **"Aktif mi?"** değerleri
  buradan taşınır. Hem kullanıcının orijinal dosyası hem bu skill'in ürettiği
  dosyalar okunabilir. Yoksa roller kaynak kolonlardan türetilir.
- `--aliases`: isim düzeltme sözlüğü. Verilmezse skill klasöründeki
  `aliases.json` kullanılır (ör. "Belgin Ylmaz" → "Belgin Yılmaz").

Çıktıdaki formüller openpyxl ile yazılır; teslimden önce xlsx skill'inin
`recalc.py` scripti ile yeniden hesaplatıp `total_errors: 0` olduğunu doğrula.

## Adımlar (kullanıcının yöntemi)

1. **Ürünler** → board'u olan her satır için `Board_Ürün` anahtarı
   (`Board Name/Product Grouping` + "_" + `Ürün Adı`) ve `TAKIM` birleşimi
   (**Ürünler kopya** sayfası, formüllerle).
2. **Board list** → unique board'lar (boşluk farkları birleştirilir); her board'un
   ekibi = tüm ürünlerindeki kişilerin tekrarsız birleşimi, kolonlara bölünmüş
   (sıra: SM, PO, yazılımcılar, analistler, testçiler). İsimler `,` `;` ve hücre içi
   alt satır ile ayrılır; `-`, `.` gibi boş işaretler atlanır.
3. **Board-ürün list** → aynı dağılımın ürün (Board_Ürün) bazlı detayı.
4. **Takımlardaki kişiler unique** → kişi, ana rol, **farklı board sayısı**
   (`COUNTIF` ile Board list üzerinden), Board_Ürün sayısı, tüm rolleri, rol
   kaynağı, çalıştığı board'lar. En çok board'a dağılmış kişi en üstte.
   - Ana rol: önceki heatmap'te varsa o; yoksa kişinin en sık geçtiği kolon
     (eşitlikte Yazılımcı > Analist > Testçi > Product Owner > Scrum Master).
5. **Final transposed_board heatmap** → her **board** için 4 satırlık blok:
   isimler / rol / kişinin kaç **farklı board**'da olduğu / boş. Board'un birden çok
   ürünü varsa ekip birleşik gösterilir ("Rol (N ürün)"). Solda PO, SM (board'un
   tüm ürünlerindeki değerler),
   "PO - SM Aynı mı?" (sarı), "Takım kişi sayısı" (≥9 kırmızı), "Aktif mi?".
   Sayı satırları tek bir renk skalasıyla boyanır: 1 = yeşil (#57BB8A),
   medyan = sarı (#FFD666), en yüksek = kırmızı (#E67C73).
6. **Özet** → toplamlar ve en çok dağılmış ilk 20 kişi (darboğaz adayları).
7. **Veri kalitesi** → kişisi olmayan (heatmap'e alınmayan) satırlar, aliases ile
   düzeltilen isimler, benzer yazılmış isimler, tek hücrede ayraçsız iki kişi
   olabilecek değerler.

## Sonuçta kullanıcıya söylenecekler

- Kaç board / Board_Ürün / unique kişi çıktığı ve önceki dosyaya göre değişim.
- En çok board'a dağılmış ilk ~10 kişi (rolüyle).
- **Veri kalitesi** sayfasındaki maddeler: yeni benzer isimleri kullanıcıya sor;
  doğrulananları `aliases.json`'a ekle ve yeniden üret.
- Rolü "Türetildi" olan yeni kişileri kontrol etmesini öner.

## Notlar

- Heatmap ölçüsü **farklı board sayısıdır** (kullanıcı isteği): aynı board'un
  birden çok ürünü tek sayılır. "Board_Ürün sayısı" yalnızca detay olarak verilir.
- `COUNTUNIQUE` (Google Sheets) yerine Excel uyumlu `COUNTA` kullanılır; kişiler
  satır içinde zaten tekilleştirildiği için sonuç aynıdır.
