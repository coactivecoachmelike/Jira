#!/usr/bin/env node
// build_slides.py çıktısındaki her slaytı 1920x1080 PNG olarak kaydeder.
// Kullanım: node export_png.mjs slaytlar.html [çıktı-öneki]
// Gerekli: playwright (npm i -D playwright && npx playwright install chromium)
// PLAYWRIGHT_CHROMIUM yolu verilirse o tarayıcı kullanılır.
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const [, , input, prefixArg] = process.argv;
if (!input) { console.error('Kullanım: node export_png.mjs slaytlar.html [önek]'); process.exit(1); }
const prefix = prefixArg || input.replace(/\.html?$/i, '');

let pw;
try { pw = await import('playwright'); }
catch {
  try { pw = await import(process.env.PLAYWRIGHT_MODULE || '/opt/node22/lib/node_modules/playwright/index.mjs'); }
  catch { console.error('playwright bulunamadı: npm i -D playwright'); process.exit(1); }
}
const browser = await pw.chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1952, height: 1200 }, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(path.resolve(input)).href, { waitUntil: 'networkidle' }).catch(() => {});
await page.evaluate(() => document.fonts && document.fonts.ready);
// Fontlar yüklendikten sonra listeyi yeniden sığdır
await page.reload({ waitUntil: 'networkidle' }).catch(() => {});
const slides = await page.$$('.slide');
for (let i = 0; i < slides.length; i++) {
  const out = `${prefix}-${i + 1}.png`;
  await slides[i].screenshot({ path: out });
  console.log(out);
}
console.log('liste yazı boyutu:', await page.evaluate(() => document.body.dataset.fitted), 'px');
await browser.close();
