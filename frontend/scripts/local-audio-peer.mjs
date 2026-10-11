/** Model-free WebRTC peer. All SDP and synthetic tone stay on loopback. */
import http from "node:http";
import { chromium } from "@playwright/test";

const browser = await chromium.launch({ headless: true, args: ["--autoplay-policy=no-user-gesture-required", "--disable-background-timer-throttling",
  "--disable-features=WebRtcHideLocalIpsWithMdns"] });
const page = await browser.newPage();
await page.goto("about:blank");
await page.evaluate(() => { window.peers = []; window.offerDelay = 0; });
const server = http.createServer(async (request, response) => {
  try {
    const path = request.url;
    let result;
    if (path === "/offer") {
      const chunks = []; for await (const chunk of request) chunks.push(chunk);
      const { sdp } = JSON.parse(Buffer.concat(chunks).toString());
      result = await page.evaluate(async (sdp) => {
        if (window.offerDelay) await new Promise(resolve => setTimeout(resolve, window.offerDelay));
        const pc = new RTCPeerConnection();
        const audio = new AudioContext(); await audio.resume();
        const tone = audio.createOscillator(); tone.frequency.value = 440;
        const dest = audio.createMediaStreamDestination(); tone.connect(dest); tone.start();
        pc.ondatachannel = ({ channel }) => { channel.onopen = () => channel.send(JSON.stringify({ type: "session.started" })); };
        pc.ontrack = ({ track }) => { window.lastRemoteTrack = track; };
        await pc.setRemoteDescription({ type: "offer", sdp });
        pc.addTrack(dest.stream.getAudioTracks()[0], dest.stream);
        await pc.setLocalDescription(await pc.createAnswer());
        await new Promise(resolve => {
          if (pc.iceGatheringState === "complete") return resolve();
          pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === "complete") resolve(); };
          setTimeout(resolve, 2000);
        });
        window.peers.push({ pc, audio, tone });
        // The emulator reaches host sockets through 10.0.2.2. Host ICE
        // candidates are bound locally; advertise that emulator route.
        return { sdp: pc.localDescription.sdp.replace(/^(a=candidate:.* udp \d+ )\S+( \d+ typ host.*)$/gim, '$110.0.2.2$2') };
      }, sdp);
    } else if (path === "/stats") {
      result = await page.evaluate(async () => {
        const current = window.peers.at(-1); if (!current) return { peers: 0 };
        const stats = [...(await current.pc.getStats()).values()];
        const inbound = stats.filter(row => row.type === "inbound-rtp" && row.kind === "audio");
        const outbound = stats.filter(row => row.type === "outbound-rtp" && row.kind === "audio");
        return { peers: window.peers.length, connection: current.pc.connectionState,
          received_packets: inbound.reduce((n, row) => n + row.packetsReceived, 0), sent_packets: outbound.reduce((n, row) => n + row.packetsSent, 0) };
      });
    } else if (path === "/disconnect") {
      await page.evaluate(() => window.peers.at(-1)?.pc.close()); result = { closed: true };
    } else if (path === "/delay") {
      await page.evaluate(() => { window.offerDelay = 4000; }); result = { delayed: true };
    } else if (path === "/ready") result = { ready: true };
    else { response.writeHead(404); response.end(); return; }
    response.writeHead(200, { "Content-Type": "application/json" }); response.end(JSON.stringify(result));
  } catch (error) { response.writeHead(500); response.end(JSON.stringify({ error: error.message })); }
});
server.listen(Number(process.argv[2]), "127.0.0.1");
process.on("SIGTERM", async () => { server.close(); await browser.close(); process.exit(0); });
