// Start, stop and restart Jarvis. Stop and restart confirm first.
import { toast, confirmDialog, noticeDialog, errText } from "./util.js";
import { post } from "./api.js";
import { restartDone, refreshStatus } from "./store.js";

export async function serviceAction(action) {
  if (action !== "start") {
    const word = action === "stop" ? "Stop" : "Restart";
    const ok = await confirmDialog({
      title: `${word} Jarvis?`,
      body: [action === "stop"
        ? "Jarvis will stop listening and talking until you start it again. Anything it is doing right now is cut off."
        : "Restarting takes a few seconds and interrupts anything Jarvis is doing: a spoken answer, a timer announcement, a running project.",
        "The light shows grey until it's back."],
      confirmLabel: word, danger: action === "stop",
    });
    if (!ok) return null;
  }
  try {
    const r = await post("api/service", { action });
    if (action !== "stop") restartDone();
    toast(r.message || `Jarvis ${action} sent.`);
    setTimeout(refreshStatus, 800);
    return r;
  } catch (e) {
    if (e.status === 400) {
      // The server refuses to start or restart Jarvis when .env wouldn't load, and says what's wrong.
      await noticeDialog(`Jarvis wasn't ${action === "stop" ? "stopped" : action === "start" ? "started" : "restarted"}`,
        [errText(e), "Jarvis was left as it was."]);
    } else {
      toast(`Couldn't ${action} Jarvis: ${errText(e)}`, "error");
    }
    return null;
  }
}
