---
name: gelistirmeler-raporu
description: GitHub Projects (v2) panosundaki işlerden "Geliştirmeler" listesi raporu üretir — statü renkli bullet'lar, sayfa üstünde statü lejantı ve Original Estimate üzerinden planlanan vs. tamamlanan iş grafikleri (KPI'lar, ilerleme çubuğu, statü ve kişi bazında efor). Kullanıcı "geliştirmeler listesi", "geliştirmeler raporu/sunumu", "proje panosundan liste çıkar", "planlanan vs tamamlanan", "borusanotomotiv project 34" gibi bir şey istediğinde veya bir github.com/orgs/<org>/projects/<n>/views/<v> linki verip liste/rapor istediğinde kullan.
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
