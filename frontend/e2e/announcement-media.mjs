/** Isolated evaluation audio bridge: audio-thread injection and gap-accounted capture. */
export function installAudioBridge() {
  const native = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  let active, sequence = 0;
  const archives = [], peers = [];
  const NativePeer = window.RTCPeerConnection;
  window.RTCPeerConnection = class extends NativePeer { constructor(...args) { super(...args); peers.push(this); } };
  const processor = `class Capture extends AudioWorkletProcessor {
    constructor(options) {
      super(); this.inject = options.processorOptions.inject; this.queue = [];
      this.buffer = new Float32Array(1024); this.used = 0;
      this.port.onmessage = ({data}) => {
        if (data.type === 'samples') this.queue.push({...data, cursor:0});
        if (data.type === 'flush') { this.flush(); this.port.postMessage({type:'flushed', id:data.id}); }
      };
    }
    flush() {
      if (!this.used) return;
      const values = this.buffer.slice(0,this.used);
      this.port.postMessage({type:'chunk', sample:this.start, values},[values.buffer]); this.used = 0;
    }
    process(inputs,outputs) {
      const out = outputs[0][0];
      if (this.inject) {
        let offset = 0;
        while (this.queue.length && offset < out.length) {
          const item = this.queue[0], n = Math.min(out.length-offset,item.samples.length-item.cursor);
          out.set(item.samples.subarray(item.cursor,item.cursor+n),offset); offset += n; item.cursor += n;
          if (item.cursor === item.samples.length) {
            this.queue.shift(); this.port.postMessage({type:'finished',id:item.id,sample:currentFrame+offset});
          }
        }
      }
      const values = this.inject ? out : (inputs[0]?.[0] || out);
      for (let offset=0; offset<values.length;) {
        if (!this.used) this.start = currentFrame+offset;
        const n = Math.min(this.buffer.length-this.used,values.length-offset);
        this.buffer.set(values.subarray(offset,offset+n),this.used); this.used += n; offset += n;
        if (this.used === this.buffer.length) this.flush();
      }
      return true;
    }
  }; registerProcessor('announcement-capture',Capture);`;
  function encode(values) {
    const buffer = new ArrayBuffer(values.length*2), view = new DataView(buffer);
    for(let i=0;i<values.length;i++) view.setInt16(i*2,Math.max(-32768,Math.min(32767,Math.round(values[i]*32767))),true);
    return btoa(String.fromCharCode(...new Uint8Array(buffer)));
  }
  function nodeFor(h,key,inject,element) {
    const node = new AudioWorkletNode(h.context,'announcement-capture',{
      numberOfInputs:inject?0:1, numberOfOutputs:1, outputChannelCount:[1], processorOptions:{inject}});
    const segment = {id:++sequence,kind:key,first_sample:null,end_sample:null,gaps:[],frames:0};
    h.segments.push(segment);
    node.onprocessorerror = () => h.errors.push({type:'processor_error',segment:segment.id});
    node.port.onmessage = ({data}) => {
      if(data.type === 'finished') {
        h.queue.get(data.id)?.(h.epoch+data.sample/h.context.sampleRate*1000); h.queue.delete(data.id);
      } else if(data.type === 'flushed') {
        h.flushes.get(data.id)?.(); h.flushes.delete(data.id);
      } else if(data.type === 'chunk') {
        const {sample,values} = data;
        if(segment.end_sample !== null && sample !== segment.end_sample) segment.gaps.push({start:segment.end_sample,end:sample});
        segment.first_sample ??= sample; segment.end_sample = sample+values.length; segment.frames += values.length;
        h[key].push({sample,data:encode(values),segment:segment.id});
        if(key === 'output') {
          if(values.some(v=>Math.abs(v)>.006)) h.lastOutputEpoch = h.epoch+segment.end_sample/h.context.sampleRate*1000;
          h.playback.push({epoch_ms:Date.now(),position:element.currentTime,paused:element.paused,
            audio_start_ms:h.epoch+sample/h.context.sampleRate*1000,audio_end_ms:h.epoch+segment.end_sample/h.context.sampleRate*1000,
            muted:element.muted,volume:element.volume,track_id:h.remote.getAudioTracks().map(t=>t.id).join(','),
            track_live:h.remote.getAudioTracks().some(t=>t.readyState==='live')});
        }
      }
    };
    h.nodes.push(node); return node;
  }
  async function flush(h) {
    if(h.context.state==='closed') return;
    await Promise.all(h.nodes.map(node=>new Promise((resolve,reject)=>{
      const id=++sequence, timer=setTimeout(()=>{h.flushes.delete(id);reject(new Error('Capture flush timed out'));},3000);
      h.flushes.set(id,()=>{clearTimeout(timer);resolve();}); node.port.postMessage({type:'flush',id});
    })));
  }
  function enqueue(samples,resolve=()=>{}) {
    const id=++sequence; active.queue.set(id,resolve); active.inputNode.port.postMessage({type:'samples',id,samples},[samples.buffer]);
  }
  const bridge = {
    async speak(data) {
      if(!active) throw new Error('Microphone not requested');
      const raw=Uint8Array.from(atob(data),c=>c.charCodeAt(0)), decoded=await active.context.decodeAudioData(raw.buffer);
      return new Promise(resolve=>enqueue(decoded.getChannelData(0).slice(),resolve));
    },
    enqueuePCM(data) {
      if(!active) throw new Error('Microphone not ready');
      const raw=Uint8Array.from(atob(data),c=>c.charCodeAt(0)), view=new DataView(raw.buffer), samples=new Float32Array(raw.length/2);
      for(let i=0;i<samples.length;i++) samples[i]=view.getInt16(i*2,true)/32768;
      enqueue(samples);
    },
    async transport() {
      const values=[];
      for(const peer of peers.filter(p=>p.connectionState!=='closed')) {
        const reports=await peer.getStats();
        values.push({epoch_ms:Date.now(),connection:peer.connectionState,
          senders:peer.getSenders().filter(s=>s.track?.kind==='audio').map(s=>({enabled:s.track.enabled,state:s.track.readyState})),
          reports:[...reports.values()].filter(s=>['outbound-rtp','inbound-rtp','media-source','remote-inbound-rtp'].includes(s.type))
            .filter(s=>s.kind==='audio'||s.mediaType==='audio').map(s=>JSON.parse(JSON.stringify(s)))});
      }
      return values;
    },
    speaking(){return Boolean(active?.queue.size);},
    activity(){return {lastOutputEpoch:active?.lastOutputEpoch||0,speaking:Boolean(active?.queue.size)};},
    drainOutput(){if(!active)return [];const result=active.output.slice(active.relayCursor||0);active.relayCursor=active.output.length;return result;},
    attachRemote() {
      const element=document.querySelector('audio');
      if(!active||!element?.srcObject)return false;
      if(active.remote===element.srcObject)return true;
      active.remoteNode?.disconnect();active.remoteTap?.disconnect();active.remote=element.srcObject;
      const source=active.context.createMediaStreamSource(element.srcObject),tap=nodeFor(active,'output',false,element);
      const quiet=active.context.createGain();quiet.gain.value=0;
      source.connect(tap);tap.connect(quiet);quiet.connect(active.context.destination);
      active.remoteNode=source;active.remoteTap=tap;return true;
    },
    async snapshot() {
      if(!active)return null;
      await flush(active);
      const histories=[...archives,active],epoch=histories[0].epoch,rate=active.context.sampleRate;
      const combine=key=>histories.flatMap(h=>h[key].map(chunk=>({...chunk,sample:chunk.sample+Math.round((h.epoch-epoch)/1000*rate)})));
      const segments=histories.flatMap(h=>h.segments.map(s=>({...s,epoch_ms:h.epoch})));
      return {input:combine('input'),output:combine('output'),playback:histories.flatMap(h=>h.playback),rate,
        totalSamples:Math.ceil(((active.epoch-epoch)/1000+active.context.currentTime)*rate),epoch_ms:epoch,
        started_epoch_ms:histories[0].startedEpoch,context_state:active.context.state,callbacks:active.input.length,
        pending:active.queue.size,replaced_streams:bridge.replaced||0,recorder:'audio_worklet_v1',coverage:segments,
        recording_complete:segments.some(s=>s.kind==='output'&&s.frames>0)&&segments.every(s=>s.frames>0&&!s.gaps.length)&&histories.every(h=>!h.errors.length),
        errors:histories.flatMap(h=>h.errors)};
    }
  };
  Object.defineProperty(window,'__announcementEval',{value:bridge,configurable:true});
  Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,value:async constraints=>{
    if(!constraints?.audio||constraints.video)return native(constraints);
    if(active){await flush(active);for(const resolve of active.queue.values())resolve(Date.now());active.queue.clear();archives.push(active);await active.context.close();bridge.replaced=(bridge.replaced||0)+1;}
    const context=new AudioContext({sampleRate:24000}),url=URL.createObjectURL(new Blob([processor],{type:'text/javascript'}));
    try{await context.audioWorklet.addModule(url);}finally{URL.revokeObjectURL(url);}
    active={context,queue:new Map(),flushes:new Map(),nodes:[],segments:[],errors:[],input:[],output:[],playback:[],startedEpoch:Date.now()};
    await context.resume();active.epoch=Date.now()-context.currentTime*1000;
    const destination=context.createMediaStreamDestination(),input=nodeFor(active,'input',true);
    active.inputNode=input;input.connect(destination);return destination.stream;
  }});
}
/** Observation only: never sends audio, context, or a speech command. */
export async function waitForPlaybackQuiet(readActivity, { maxMs = 30000, quietMs = 2000,
  now = () => Date.now(), pause = ms => new Promise(resolve => setTimeout(resolve, ms)) } = {}) {
  const started = now();
  let last = await readActivity();
  while (now() - last.lastOutputEpoch < quietMs && now() - started < maxMs) {
    await pause(100);
    last = await readActivity();
  }
  return { extended_ms: now() - started, exhausted: now() - last.lastOutputEpoch < quietMs,
    last_voiced_epoch_ms: last.lastOutputEpoch, quiet_ms: quietMs };
}

export function playbackQuietObserver(readActivity, options = {}) {
  let remaining = options.maxMs ?? 30000;
  return async () => {
    const result = await waitForPlaybackQuiet(readActivity, {...options, maxMs: remaining});
    remaining = Math.max(0, remaining - result.extended_ms);
    return {...result, remaining_ms: remaining};
  };
}
