// Connects to Frappe's socket.io as a logged-in user and prints chikku_ai_event events as JSON lines.
const path = require("path");
const { io } = require(path.join("/home/frappe/frappe-bench/apps/frappe/node_modules", "socket.io-client"));
const [sid, site, seconds, publicHost] = process.argv.slice(2);

const socket = io(`http://127.0.0.1:9000/${site}`, {
	transports: ["websocket"],
	extraHeaders: { Cookie: `sid=${sid}`, Host: publicHost, Origin: `https://${publicHost}`, "X-Frappe-Site-Name": site },
	reconnection: false,
});
socket.on("connect", () => console.log(JSON.stringify({ listener: "connected" })));
socket.on("connect_error", (e) => console.log(JSON.stringify({ listener: "error", message: e.message })));
socket.on("chikku_ai_event", (evt) => console.log(JSON.stringify({ event: "chikku_ai_event", data: evt })));
socket.on("list_update", (evt) => console.log(JSON.stringify({ event: "list_update", data: evt })));
setTimeout(() => process.exit(0), Number(seconds || 60) * 1000);
