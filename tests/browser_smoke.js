// Requires the explicit synthetic-display fixture in desktop_fixture.py.
// This checks the real web controls and audio decoder, not native Windows capture.
const {chromium} = require('playwright');
(async () => {
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1200,height:1000}});
  const errors=[];
  page.on('pageerror', error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:18766/');
  await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Выделите'));
  await page.locator('#capture').click();
  await page.locator('#calibration').waitFor({state:'visible'});
  await page.locator('#screen').scrollIntoViewIfNeeded();
  const box=await page.locator('#screen').boundingBox();
  await page.mouse.move(box.x+610/1600*box.width,box.y+80/900*box.height);
  await page.mouse.down();
  await page.mouse.move(box.x+838/1600*box.width,box.y+212/900*box.height,{steps:8});
  await page.mouse.up();
  await page.locator('#saveRegion').click();
  await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Индикатор найден'));
  await page.locator('#start').click();
  await page.waitForFunction(()=>document.querySelector('#message').textContent==='Воспроизвожу',null,{timeout:40000});
  await page.locator('#pause').click();
  await page.waitForFunction(()=>document.querySelector('#message').textContent==='На паузе');
  await page.locator('#cache').fill('64');
  await page.locator('#volume').fill('55');
  await page.locator('#settings').click();
  await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Настройки сохранены'));
  await page.locator('#clear').click();
  await page.waitForFunction(()=>document.querySelector('#stats').textContent.includes('записей: 0'));
  if(process.env.WCA_SCREENSHOT) await page.screenshot({path:process.env.WCA_SCREENSHOT,fullPage:true});
  if(errors.length) throw Error(errors.join('\n'));
  await page.locator('#quit').click();
  await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Программа завершена'));
  await browser.close();
  console.log('Browser smoke passed: calibration, optical reception, real audio download/decode, pause, settings, cache clear and shutdown.');
})().catch(error=>{console.error(error);process.exitCode=1;});
