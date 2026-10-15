---
name: gelistirmeler-raporu
description: GitHub Projects (v2) panosundaki işlerden "Geliştirmeler" listesi raporu ve 4 slaytlık sprint sunumu (Sprint'te Odaklandığımız İşler, Metriklerimiz, Metriklerimiz (Takıma özel), Canlıya Çıkmaya Hazır Olan İşler) üretir — statü renkli bullet'lar, sayfa üstünde statü lejantı ve Original Estimate üzerinden planlanan vs. tamamlanan iş grafikleri (KPI'lar, ilerleme çubuğu, statü ve kişi bazında efor). Kullanıcı "geliştirmeler listesi", "geliştirmeler raporu/sunumu", "proje panosundan liste çıkar", "planlanan vs tamamlanan", "sprint sunumu", "metriklerimiz", "canlıya hazır işler", "borusanotomotiv project 34" gibi bir şey istediğinde veya bir github.com/orgs/<org>/projects/<n>/views/<v> linki verip liste/rapor istediğinde kullan.
---

# Geliştirmeler Raporu

Bir GitHub organizasyon projesinin (Projects v2) item'larından, sunum slaytındaki
"Geliştirmeler" görseline benzeyen tek dosyalık bir HTML raporu üretir:

1. **Başlık + statü lejantı** — sayfanın üstünde her statü rengi, adı ve iş adedi.
   `✓` işaretli statüler "tamamlandı" sayılır. Lejanttaki statüye fare ile gelince
   GitHub'daki statü açıklaması görünür.
2. **Planlanan vs. tamamlanan iş** — `Original Estimate` alanının toplamı üzerinden:
   Toplam planlanan / Tamamlanan / Kalan / Tamamlanma oranı kutuları ve ilerleme çubuğu.
3. **Grafikler** — statü bazında efor dağılımı (statü renkli parça-bütün şeridi + tablo)
   ve kişi bazında planlanan vs. tamamlanan (bullet bar). Üzerine gelince tooltip.
4. **İş listesi** — 3 kolonlu, her iş başında **statü renginde bullet**, sağda efor
   rozeti, başlık issue/PR linkine gider. Varsayılan sıralama statü sırasına göredir.

Bullet renkleri doğrudan GitHub'daki Status seçeneklerinin renkleridir
(GRAY, BLUE, GREEN, YELLOW, ORANGE, RED, PINK, PURPLE); statüsü olmayan işler açık gri
"Statüsüz" olarak gösterilir.

## Varsayılanlar

Kullanıcının verdiği link:
`https://github.com/orgs/borusanotomotiv/projects/34/views/11?visibleFields=[…,402706858,…]`

| Parametre | Varsayılan | Not |
|---|---|---|
| `--org` | `borusanotomotiv` | URL'deki `orgs/<org>` |
| `--project` | `34` | URL'deki `projects/<n>` |
| `--view` | `11` | Görünümün kayıtlı filtresi uygulanır (`0` = filtre yok) |
| `--estimate-field-id` | `402706858` | `visibleFields` içindeki sayısal alan = Original Estimate |
| `--estimate-field` | `Original Estimate` | id bulunamazsa isimle, o da yoksa adında "estimate/tahmin" geçen sayı alanı |
| `--status-field` | `Status` | |
| `--done-status` | Done, Tamamlandı, Completed, Closed, Kapandı, Canlıda, Released, Yayında | Tekrarlanabilir; verilirse listeyi tamamen değiştirir |

Başka bir proje linki verilirse org / project / view değerlerini URL'den al; URL'de
`visibleFields` içinde sayısal bir id varsa ve kullanıcı efor alanından söz ediyorsa
onu `--estimate-field-id` olarak geç.

## Çalıştırma

Gereken: Python 3.9+ (yalnızca standart kütüphane) ve proje okuma yetkisi olan bir
GitHub kimliği — ya `gh auth login -s read:project` (veya
`gh auth refresh -s read:project`) ya da `read:project` kapsamlı bir `GITHUB_TOKEN`.
Organizasyon SSO kullanıyorsa token'ın o org için yetkilendirilmiş olması gerekir.

```bash
S=.claude/skills/gelistirmeler-raporu/scripts/build_report.py

# Canlı veriden (view 11 filtresiyle)
python3 $S -o gelistirmeler.html --unit SP --dump-json proje.json

# Sadece belirli bir sprint / ek filtre
python3 $S --extra-filter 'iteration:"Sprint 42"' -o sprint42.html

# Görünüm filtresi yerine kendi filtren
python3 $S --filter 'status:Done,"In Progress" -label:bug' -o rapor.html

# Kapalı issue'ları statüden bağımsız tamamlandı say, özel "done" statüleri
python3 $S --closed-as-done --done-status "Done" --done-status "Canlıya Alındı"

# GitHub'dan indirilen tablo (Projects > görünüm menüsü > Export view data, TSV/CSV)
python3 $S --from-export "BOD Project - Retail Sprints.tsv" --clean-titles -o gelistirmeler.html

# Ağ olmadan, daha önce kaydedilmiş veriden
python3 $S --from-json proje.json -o gelistirmeler.html
```

Diğer seçenekler: `--title` (varsayılan "Geliştirmeler"), `--unit` (efor birimi etiketi:
`SP`, `sa`, `gün`…), `--sort status|title|none`, `--columns N`, `--include-archived`,
`--me <login>` (filtredeki `@me` için). Tümü için `--help`.

## Adımlar

1. Kullanıcının linkinden org / proje / görünüm numarasını çıkar; farklı değilse
   varsayılanları kullan. Efor biriminin ne olduğunu (SP / saat / gün) biliyorsan
   `--unit` ver; bilmiyorsan boş bırak.
2. Betiği `--dump-json` ile çalıştır. Kimlik hatası alırsan kullanıcıdan
   `gh auth refresh -s read:project` çalıştırmasını veya token tanımlamasını iste —
   token'ı kendin aramaya çalışma.
3. Betiğin konsol çıktısındaki iş adedi, planlanan/tamamlanan toplam ve uygulanan
   filtreyi kullanıcıya bildir. Rapor altındaki notlarda "tahmini boş" iş sayısı ve
   atlanan filtreler yazar; bunları da aktar.
4. Çıktıyı göster: HTML dosyasını kullanıcıya gönder veya Artifact olarak yayınla.
   Slayta koymak için PNG gerekiyorsa Playwright/Chromium ile ekran görüntüsü al
   (1400px genişlik, `fullPage: true`) ya da tarayıcıdan yazdır.
5. Statü sırası/rengi yanlış görünüyorsa bunlar GitHub'daki Status alanından gelir;
   betikte değil projede düzeltilmesi gerekir.

## Sprint sunumu (4 slayt, 16:9, HTML + PNG)

Kullanıcı slayt / sunum / sprint sunumu / 16:9 / PNG isterse `build_slides.py` kullan
(veri kaynağı seçenekleri `build_report.py` ile aynı). Çıktı her zaman şu 4 slayttır:

1. **Sprint'te Odaklandığımız İşler** — işler **Xurrent Talep No**'ya göre gruplanır
   (aynı talebin BE/FE/Jarvis işleri tek satır). Talep no'su olmayan işler, sade
   başlıkları aynıysa (yalnızca BE/FE son eki farklı) birleşir. Her satırda:
   statü renginde bullet (grubun **en geride** kalan işinin statüsü), kategori etiketi
   (User Story / Bug / Task / Proje işi), iş birimi diliyle **tema adı**, küçük `#talep no`,
   sağda **"N talep"** (grubun GitHub işi adedi). `Original Sprint` alanı dolu olan
   gruplar sarı **"↪ Aktarıldı: Sprint X"** etiketi ve sarı satır vurgusuyla işaretlenir.
2. **Metriklerimiz** — yalnız adet bazlı, OE yok (iş tipi hariç):
   - Planlanan vs. tamamlanan: **Toplam** (Xurrent + ek), **distinct Xurrent talebi**
     (talep, tüm GitHub işleri bitince tamamlanmış sayılır) ve **ek talepler**
     (Xurrent no'suz GitHub işleri) — üç ayrı oran.
   - Statü dağılımı: iki kolon — Xurrent talebi (grubun en geri statüsü) | GitHub işi.
   - İş tipi kırılımı: GitHub işi | Original Estimate (planlanan/tamamlanan).
   - Sprint ortasında gelen iş: `AddedMidSprint` etiketli ÷ ilk planlanan (etiketsiz),
     hem iş adedi hem OE; altında o işlerin tema adları.
   - Teknik borç payı: `TB` etiketli ÷ toplam, hem iş adedi hem OE; altında tema adları.
   - Lead time histogramı (**GitHub işi** bazında, talep bazında değil — slaytta not olarak yazar; Closed tarihi boş biten işler hariç tutulur ve numaraları notta listelenir): yalnızca **Done ve Released** statüsündeki
     işler (`--lead-status`; Closed tarihi olup başka statüde olanlar sayılmaz). Süre,
     sprint başlangıcından Closed tarihine kadar geçen gün. `Original Sprint` doluysa o
     sprintin, boşsa işin `Sprint` alanındaki sprintin başlangıcı esas alınır; önceki
     sprintler mevcut sprintten geriye her biri `--sprint-days` (14) gün sayılarak bulunur.
3. **Metriklerimiz (Takıma özel)** — Original Estimate bazında planlanan vs. tamamlanan
   (toplam / Xurrent talepleri / ek talepler; iş bazında), statü dağılımı (OE) ve kişi
   bazında planlanan vs. tamamlanan (GitHub işi | OE).
4. **Canlıya Çıkmaya Hazır Olan İşler** — statüsü `Done` **ve** `Ready For Release`
   etiketli işler, slayt 1'deki gibi gruplanmış tema adlarıyla. Yanında ayrı başlıkla
   "Bu sprintte canlıya alınanlar": aynı etiketli olup Released vb. bitmiş statüdeki işler.

### Akış

```bash
S=.claude/skills/gelistirmeler-raporu/scripts
D=sunum/<takım>-<yyyy-mm-dd>
# 1) Grupları ve varsayılan adları çıkar
python3 $S/build_slides.py --from-export veri.tsv --team "<Takım>" --dump-themes $D/temalar-taslak.json -o $D/slaytlar.html
# 2) temalar.json yaz: her grup anahtarı → iş biriminin anlayacağı tema adı
# 3) Son hali üret ve PNG'ye çevir
python3 $S/build_slides.py --from-export veri.tsv --team "<Takım>" --sprint-start "Sprint 4=2026-10-05" --themes $D/temalar.json -o $D/slaytlar.html
node $S/export_png.mjs $D/slaytlar.html $D/slayt      # -> slayt-1..4.png (1920x1080)
```

Önceki bir sprintin `temalar.json`'u varsa yeni taslaktaki başlıkları eskisininkilerle
eşleştirerek adları taşı, yalnızca yeni temaları yaz.

**Tema adları (2. adım) Claude'un işidir:** taslaktaki her grubun `titles` listesine
bakıp müşterinin / iş biriminin anlayacağı kısa bir ad yaz — BE/FE/servis/Jarvis gibi
teknik ekleri, talep numarasını, "Hk." gibi kısaltmaları at; ne yapıldığını sonuç
diliyle söyle (ör. "Yeni Üyelik | ... Hatırlatma Maili BE/FE" → "Evraklarını
tamamlamayan yeni üyelere otomatik hatırlatma e-postası"). Başlıkta olmayan bilgi
uydurma; emin değilsen başlığa sadık kal. Bug'larda sorunu anlat ("... yaşanan hata").
`temalar.json` biçimi `{"<grup anahtarı>": "<ad>"}`; anahtarlar taslaktakiyle birebir
aynı olmalı. Dosyayı çıktı klasöründe sakla (sonraki sprintte aynı talepler için
yeniden kullanılabilir).

### Alanlar ve etiketler

| Seçenek | Varsayılan | Not |
|---|---|---|
| `--xurrent-field` | `Xurrent Talep No` | Sütun esastır (başlıktaki numaradan farklı olabilir). Sütun yoksa ya da hücre boşsa başlığın başındaki numara (`82634239 - …`, `+82634239 - …`) kullanılır; boş hücre için uyarı basılır — kullanıcıya bildir |
| `--origin-field` | `Original Sprint` | Değer `3` ya da `Sprint 3` olabilir. İşin `Sprint`'inden farklıysa "aktarıldı" belirteci gösterilir (aynıysa gösterilmez); sütun yoksa uyarı verir |
| `--sprint-field` | `Sprint` | Alt başlıkta gösterilir; lead time başlangıcı |
| `--sprint-start` | — | **Zorunlu (lead time için):** `'Sprint 4=2026-10-05'`; tekrarlanabilir. Tanımlanmayan sprintler numarası ve `--sprint-days` (14) ile türetilir. Export'ta sprint tarihi yoktur — kullanıcıya sor veya bilinen bir tarihten çıkar ve varsayımı söyle |
| `--mid-label` | `AddedMidSprint` | Sprint ortasında gelen iş |
| `--tb-label` | `TB`, `Teknik Borç`, `Tech Debt` | Teknik borç |
| `--ready-label` / `--ready-status` | `Ready For Release` / `Done` | Canlıya hazır |
| `--team` | proje adı | Alt başlıktaki takım adı |

Etiket eşleşmesi büyük/küçük harf, boşluk, `-` ve `_` farkını yok sayar.

- **İş tipi kuralı** (GitHub issue type yoksa): başlıkta `[BUG]` → Bug; `DD Project Name`
  dolu → Proje işi; başlıkta operasyonel anahtar kelime (işlemler/işlemleri, toplantı,
  takvim, kurulum, incele, migration, dependabot, destek süreci, deployment, task,
  dönüşleri, modelbuilder, dwh — düzenli ifade, Türkçe küçük harfle aranır) → Task; kalanlar
  User Story. Grubun kategorisi, işlerinin çoğunluk tipidir. Export'a `Type` sütunu
  eklenirse o kullanılır.
- Konsol çıktısındaki özet sayıları (Xurrent / ek / sprint ortası / teknik borç /
  canlıya hazır) ve uyarıları (eksik sütunlar) kullanıcıya aktar.
- PNG için Node + `playwright` gerekir; yoksa HTML'i tarayıcıda açıp yazdır → PDF.

## Dışa aktarılan TSV/CSV ile çalışma

Kullanıcı GitHub'dan indirdiği bir `.tsv`/`.csv` verirse `--from-export` kullan; API
erişimi gerekmez. Dikkat edilecekler:
- Dosyada renk bilgisi yoktur; statü renkleri isimden atanır (Backlog gri,
  **Todo kırmızı**, In Progress sarı, Review mor, Test turuncu, UAT pembe, Blocked kırmızı,
  **Done mavi**, Released yeşil; bilinmeyenler gri). Bilinen statüler iş akışı sırasına dizilir
  (Backlog → Todo → In Progress → Review → Test → UAT → Done → Released).
- Takım başına ayrı export gelirse her biri için ayrı klasöre (`sunum/<takım>/`) üret.
  Export'ta başka board'lardan satır varsa (`Board` sütununa bak) takımın board'una
  filtrele, ör. `--filter 'board:"Back Office"'`, ve dışarıda kalanları kullanıcıya söyle.
- Yalnızca görünümde açık olan sütunlar dışa aktarılır. `Original Estimate` sütunu
  yoksa grafikler **iş adedi** üzerinden çizilir ve raporda not düşülür — kullanıcıya
  eforlu oran için alanı görünüme ekleyip yeniden dışa aktarmasını öner.
- `--clean-titles` baştaki talep numaralarını (`82237192 - `) siler, `[BUG]:` → `[BUG]`.

## Filtre desteği

Görünümün kayıtlı filtresi GraphQL'den okunur ve yaygın alt kümesi yerelde uygulanır:
`is:open|closed|issue|pr|draft|archived`, `alan:değer1,değer2` (alan adları küçük harf,
boşluk yerine `-`; ör. `original-estimate:>=5`, `status:"In Progress"`), `-alan:değer`
(hariç tut), `no:alan`, `has:alan`, `assignee:`, `label:`, `repo:`, sayısal
`>`, `<`, `>=`, `<=`, `a..b` ve serbest metin (başlıkta arar). `@current`, `@next`,
`@today` gibi göreli değerler desteklenmez; atlanır ve raporda uyarı olarak yazılır —
bu durumda `--filter` ile açık değer ver.

## Veri formatı (`--dump-json` / `--from-json`)

```json
{
  "project": {"title": "...", "url": "..."},
  "view": {"name": "...", "filter": "..."},
  "statusField": "Status",
  "statusOptions": [{"name": "Done", "color": "GREEN", "description": "..."}],
  "numberFields": [{"name": "Original Estimate", "databaseId": 402706858}],
  "items": [{"title": "...", "url": "...", "type": "ISSUE", "state": "OPEN",
             "archived": false, "assignees": ["login"], "labels": [],
             "fields": {"Status": "Done", "Original Estimate": 5}}]
}
```

Örnek veri: `examples/sample.json` (görseldeki başlıklar, rastgele statü/efor) ve
ondan üretilmiş `examples/sample.png`.
