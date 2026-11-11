<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { defineStore } from 'pinia'

export type FeedItem={file_id:string;work_id:string;creator_id:string;current_path:string;title:string;creator:string;duration:number|string;reason:string}
const api=async<T>(path:string,init?:RequestInit):Promise<T>=>{const r=await fetch(path,init);if(!r.ok)throw new Error(await r.text());return r.json()}
const useFeedStore=defineStore('feed',()=>{
  const mode=ref('recommend'),items=ref<FeedItem[]>([]),index=ref(0),page=ref(0),loading=ref(false),real=ref(false),hasMore=ref(true)
  const current=computed(()=>items.value[index.value])
  async function load(reset=true){if(loading.value)return;loading.value=true;try{const p=reset?0:page.value+1;const batch=await api<FeedItem[]>(`/api/feed?mode=${mode.value}&limit=12&page=${p}`);if(reset){items.value=batch;index.value=0;page.value=0}else{items.value.push(...batch);page.value=p}hasMore.value=batch.length>0&&batch.length===12}finally{loading.value=false}}
  async function loadMore(){if(hasMore.value&&!loading.value)await load(false)}
  async function event(type:string,extra:Record<string,unknown>={}){if(!real.value||!current.value)return;await api('/api/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file_id:current.value.file_id,work_id:current.value.work_id,event_type:type,mode:'real',extra})}).catch(()=>{})}
  async function next(delta:number){const n=Math.max(0,Math.min(items.value.length-1,index.value+delta));if(n!==index.value){void event(delta>0?'skip':'rewind');index.value=n;if(index.value>=items.value.length-3)void loadMore()}}
  return{mode,items,index,current,loading,real,hasMore,load,loadMore,event,next}
})
const store=useFeedStore()
const shell=ref<HTMLElement|null>(null),viewport=ref<HTMLElement|null>(null),paused=ref(false),immersive=ref(false),startY=ref(0),moved=ref(false),clickTimer=ref<number|null>(null)
const videoRefs=ref<Record<number,HTMLVideoElement>>({}),preloadCache=new Map<number,HTMLVideoElement>(),slotIndices=computed(()=>[store.index-1,store.index,store.index+1].filter(i=>i>=0&&i<store.items.length))
function media(x:FeedItem){return`/media?path=${encodeURIComponent(x.current_path)}`}
function setVideoRef(i:number,el:Element|null){if(el)videoRefs.value[i]=el as HTMLVideoElement;else delete videoRefs.value[i]}
function warm(i:number){const x=store.items[i];if(!x||preloadCache.has(i))return;const v=document.createElement('video');v.preload='auto';v.muted=true;v.src=media(x);v.load();preloadCache.set(i,v)}
function prepare(i:number){const v=videoRefs.value[i],x=store.items[i];if(!v||!x)return;const cached=preloadCache.get(i);if(cached){v.src=cached.src;preloadCache.delete(i)}else if(v.src!==location.origin+media(x))v.src=media(x);v.preload=i===store.index?'auto':'metadata';v.muted=localStorage.getItem('muted')==='1';v.playsInline=true;v.onplay=()=>{if(i===store.index)paused.value=false};v.onpause=()=>{if(i===store.index)paused.value=true};v.onended=()=>{if(i===store.index){void store.event('complete');void store.next(1)}};if(i===store.index)v.play().catch(()=>{})}
function prepareSlots(){nextTick(()=>{for(const i of slotIndices.value)prepare(i);warm(store.index+1);warm(store.index-1);for(const [i,v] of preloadCache){if(Math.abs(i-store.index)>2){v.src='';preloadCache.delete(i)}}})}
function singleClick(){if(clickTimer.value)clearTimeout(clickTimer.value);clickTimer.value=window.setTimeout(()=>{const v=videoRefs.value[store.index];if(v)v.paused?v.play().catch(()=>{}):v.pause()},220)}
function doubleClick(){if(clickTimer.value)clearTimeout(clickTimer.value);void store.event('like')}
function touchStart(e:TouchEvent){startY.value=e.touches[0].clientY;moved.value=false}
function touchMove(e:TouchEvent){if(Math.abs(e.touches[0].clientY-startY.value)>12)moved.value=true}
function touchEnd(e:TouchEvent){const d=startY.value-e.changedTouches[0].clientY;if(moved.value&&Math.abs(d)>Math.max(48,window.innerHeight*.12))void store.next(d>0?1:-1)}
function toggleFullscreen(){if(!shell.value)return;if(document.fullscreenElement){void document.exitFullscreen();immersive.value=false}else if(shell.value.requestFullscreen){shell.value.requestFullscreen().then(()=>immersive.value=true).catch(()=>immersive.value=true)}else immersive.value=true;document.body.classList.toggle('locked',immersive.value)}
function fullscreenChanged(){if(!document.fullscreenElement)immersive.value=false;document.body.classList.toggle('locked',immersive.value)}
watch(()=>[store.index,store.items.length],prepareSlots)
onMounted(async()=>{await store.load();prepareSlots();document.addEventListener('fullscreenchange',fullscreenChanged)})
onBeforeUnmount(()=>{document.removeEventListener('fullscreenchange',fullscreenChanged);for(const v of Object.values(videoRefs.value))v.pause()})
</script>
<template>
<main ref="shell" class="player-shell" :class="{immersive}">
<header class="topbar"><button class="icon" @click="toggleFullscreen">{{immersive?'×':'⛶'}}</button><div class="tabs"><button :class="{active:store.mode==='recommend'}" @click="store.mode='recommend';store.load()">推荐</button><button :class="{active:store.mode==='creator'}" @click="store.mode='creator';store.load()">博主</button><button :class="{active:store.mode==='random'}" @click="store.mode='random';store.load()">探索</button></div><button class="real" @click="store.real=!store.real">{{store.real?'真实':'测试'}}</button></header>
<section ref="viewport" class="viewport" @click="singleClick" @dblclick="doubleClick" @touchstart="touchStart" @touchmove="touchMove" @touchend="touchEnd" @wheel.prevent="store.next($event.deltaY>0?1:-1)">
<div class="video-stage"><div v-for="i in slotIndices" :key="i" class="video-card" :class="{active:i===store.index}" :style="{transform:`translate3d(0,${(i-store.index)*100}%,0)`}"><video :ref="el=>setVideoRef(i,el)" class="video" playsinline></video></div></div>
<div v-if="store.loading&&!store.current" class="loading">加载中…</div><div v-if="!store.current&&!store.loading" class="loading">暂无视频</div>
<div v-if="store.current" class="meta"><b>@{{store.current.creator}}</b><p>{{store.current.title||'未命名视频'}}</p><small>{{store.current.reason}}</small></div>
<aside v-if="store.current" class="actions"><button @click.stop="void store.event('like')">♥</button><button @click.stop="void store.event('favorite')">☆</button><button @click.stop="store.next(1)">›</button></aside>
</section><nav class="bottom"><span class="selected">⌂ 推荐</span><span>♡ 喜欢</span><span>◉ 我的</span></nav>
</main>
</template>


