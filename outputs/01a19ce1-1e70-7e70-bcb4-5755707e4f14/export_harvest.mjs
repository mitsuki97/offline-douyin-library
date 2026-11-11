import fs from 'node:fs/promises';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';
const [statePath, outPath] = process.argv.slice(2);
const state = JSON.parse(await fs.readFile(statePath, 'utf8'));
const creators = state.creators || [];
const works = state.works || [];
const errors = state.errors || [];
const byCreator = new Map(creators.map(c => [c.url, c]));
const groups = new Map();
for (const w of works) groups.set(w.creator_url, (groups.get(w.creator_url) || 0) + 1);
const wb = Workbook.create();
const summary = wb.worksheets.add('博主汇总');
const detail = wb.worksheets.add('全部作品');
const issue = wb.worksheets.add('读取失败');
const sh = (sheet, headers, rows, widths) => {
  const matrix=[headers,...rows];
  sheet.getRangeByIndexes(0,0,matrix.length,headers.length).values=matrix;
  const all=sheet.getRangeByIndexes(0,0,matrix.length,headers.length);
  sheet.getRangeByIndexes(0,0,1,headers.length).format={fill:'#1F4E78',font:{bold:true,color:'#FFFFFF'},wrapText:true,verticalAlignment:'center'};
  all.format.wrapText=true; all.format.verticalAlignment='center';
  all.format.borders={preset:'all',style:'thin',color:'#D9E2F3'};
  sheet.getRangeByIndexes(0,0,1,headers.length).format.rowHeight=30;
  widths.forEach((w,i)=>sheet.getRangeByIndexes(0,i,matrix.length,1).format.columnWidth=w);
  sheet.freezePanes.freezeRows(1); sheet.showGridLines=false;
};
const sHeaders=['博主','主页链接','主页读取状态','发现作品数','有点赞量','有播放量','已下载','未下载/失败','绝版/不可访问'];
const sRows=creators.map(c=>{
 const xs=works.filter(w=>w.creator_url===c.url);
 return [c.name||'',c.url||'',xs.length?'已采集':'无作品或读取失败',xs.length,xs.filter(w=>Number.isFinite(w.digg_count)).length,xs.filter(w=>Number.isFinite(w.play_count)).length,xs.filter(w=>['已下载','已存在'].includes(w.download_status)).length,xs.filter(w=>!['已下载','已存在'].includes(w.download_status)).length,xs.filter(w=>String(w.availability_status||'').startsWith('绝版')).length];
});
sh(summary,sHeaders,sRows,[24,54,22,14,14,14,12,18,18]);
const dHeaders=['博主','作品ID','标题','发布时间','播放量','点赞量','收藏量','评论量','分享量','作品链接','下载状态','本地路径','读取状态','可访问状态','筛选原因','最后检查时间'];
const dRows=works.map(w=>[w.creator||'',w.aweme_id||'',w.title||'',w.publish_time||'',w.play_count??'',w.digg_count??'',w.collect_count??'',w.comment_count??'',w.share_count??'',w.url||'',w.download_status||'未尝试',w.local_path||'',w.read_status||'',w.availability_status||'正常',w.selection_reason||'',w.last_checked_at||'']);
sh(detail,dHeaders,dRows,[24,22,42,21,13,13,13,13,13,50,18,60,22,20,24,21]);
const eHeaders=['博主','主页链接','阶段/错误'];
const eRows=errors.map(e=>[e.creator||'',e.url||'',e.error||'']);
if(state.fatal_error)eRows.unshift(['','',state.fatal_error]);
sh(issue,eHeaders,eRows,[24,54,100]);
await wb.recalculate();
const inspect=await wb.inspect({kind:'sheet,region',maxChars:2200,tableMaxRows:3,tableMaxCols:8});
console.log(inspect.ndjson||inspect);
for(const [name,range] of [['博主汇总',`A1:I${Math.max(2,Math.min(creators.length+1,26))}`],['全部作品','A1:P20'],['读取失败','A1:C20']]){
 const png=await wb.render({sheetName:name,range,scale:1,format:'png'});
 const safe=name.replace(/[\\/:*?"<>|]/g,'_'); await fs.writeFile(`${outPath}.preview.${safe}.png`,new Uint8Array(await png.arrayBuffer()));
}
await fs.mkdir((await import('node:path')).dirname(outPath),{recursive:true});
const file=await SpreadsheetFile.exportXlsx(wb); await file.save(outPath);
console.log(`EXPORTED ${outPath} creators=${creators.length} works=${works.length} errors=${errors.length}`);
