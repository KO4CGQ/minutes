# Video meetings guide

Getting started with video meetings: setting up the club's own [Jitsi Meet](https://jitsi.org/jitsi-meet/) server so members join it from here, already signed in. For whoever runs the servers. Video meetings are optional: until an admin turns them on, nothing about them shows in the app. The details of each setting are in the [server admin guide](server-admin.md#video-meetings-jitsi-optional).

## 🧭 How it works
- A member opens **Video meeting** in the menu. The app checks who they are and sends them to the Jitsi server with a short-lived token saying their name, whether they host, and the room's rules.
- The Jitsi server trusts the token because both servers share a secret. It needs no passwords of its own, so members sign in to video meetings with their passkeys too.
- Anyone without a token is a guest. Guests knock in the lobby and a host lets them in; they can't start a meeting on their own.

## 📋 What you need
- **A Jitsi Meet server of the club's own**, installed the usual way ([Jitsi's self-hosting guide](https://jitsi.github.io/handbook/docs/devops-guide/devops-guide-quickstart): Debian or Ubuntu packages, or their Docker images). Use a separate machine, VM or container from this app's.
- **A public name and a certificate** for it, such as `meet.example.org` (Jitsi's installer can get a Let's Encrypt certificate).
- **Its ports open:** TCP 443 for the web page, and UDP 10000 for the audio and video. For guests behind strict firewalls, TURN (Jitsi's installer can set up coturn).
- **The token sign-in package** (`jitsi-meet-tokens` on Debian and Ubuntu; `ENABLE_AUTH=1` and `AUTH_TYPE=jwt` on Docker).
- **Two lobby modules** from [jitsi-contrib's Prosody plugins](https://github.com/jitsi-contrib/prosody-plugins): `token_lobby_autostart` and `token_lobby_bypass`.
- **This app's own module**, `mod_club_rules.lua`, from the `deploy/jitsi/` folder of the release you're running. It applies each room's rules for guests, so take it again after each update.

## 🔧 Setting up the Jitsi server
Shown for Debian's packages; Docker's images take the same settings in their own files. Replace `meet.example.org` with the server's name, and `minutes.example.org` with this app's address.

### 1. The shared secret
In this app, Admin → Site → Features → **Make a secret**. It's shown once: copy it straight into a file on the Jitsi server that only Prosody can read, such as `/etc/prosody/club-minutes-secret` (never by email or chat: anyone with it can sign in as any member).

### 2. The modules
Copy `mod_token_lobby_autostart.lua`, `mod_token_lobby_bypass.lua` and `mod_club_rules.lua` into a plugins folder, such as `/usr/local/share/prosody-plugins`.

### 3. Prosody
At the end of `/etc/prosody/conf.avail/meet.example.org.cfg.lua`, add:

```lua
plugin_paths:append { "/usr/local/share/prosody-plugins" }
asap_accepted_issuers = { "club-minutes" }  -- the app ID
asap_accepted_audiences = { "jitsi" }

VirtualHost "meet.example.org"
    authentication = "token"
    app_id = "club-minutes"
    app_secret = FileLine("/etc/prosody/club-minutes-secret")
    allow_empty_token = false
    modules_enabled:append { "persistent_lobby" }

Component "conference.meet.example.org" "muc"
    modules_enabled:append { "token_verification", "token_affiliation", "token_lobby_autostart", "token_lobby_bypass", "club_rules" }
    wait_for_host_disable_auto_owners = true
    token_verification_allowlist = { "guest.meet.example.org" }
```

Keep (or add) the guest domain: a `VirtualHost "guest.meet.example.org"` with `authentication = "jitsi-anonymous"`, and `anonymousdomain: 'guest.meet.example.org'` under `hosts` in the server's `config.js`. Keep Jitsi's AV moderation on, as it ships: the guest rules use it.

### 4. Jicofo
In `/etc/jitsi/jicofo/jicofo.conf`, so hosts come only from the token (with Jicofo's own sign-in on, every member becomes a host):

```
jicofo {
  authentication { enabled = false }
  conference { enable-auto-owner = false }
}
```

### 5. The web page
In `/etc/jitsi/meet/meet.example.org-config.js`:

```js
config.welcomePage = { disabled: true };
config.enableClosePage = true;
config.securityUi = { disableLobbyPassword: true };
```

With no meeting passwords, guests always knock. The thank-you page after hanging up stops the ended meeting from looking like it's still running.

### 6. The bare address
In the server's nginx site, send `https://meet.example.org/` to this app, so nobody lands in a random room:

```nginx
location = / { return 302 https://minutes.example.org/meet; }
```

### 7. Restart
Restart Prosody, Jicofo and the bridge one after another (all three at once can leave Jicofo disconnected, and no meeting starts), then reload nginx.

> [!TIP]
> If Prosody won't start after this on Debian 13, the token modules may be missing Lua's `inspect` for Lua 5.4: link `/usr/share/lua/5.3/inspect.lua` to `/usr/local/share/lua/5.4/inspect.lua`.

## ✅ Turning it on
1. Admin → Site → Features (in admin mode): tick **The club's own Jitsi Meet server**, enter its address (`meet.example.org`) and the app ID (`club-minutes`, the same as Prosody's), and save.
2. Admin → Site → Video: the club's rooms, who comes straight in, and what guests may do. Until you add rooms, there's one room, `club`.
3. Site → Roles: who hosts (the **host video meetings** permission; Officer and Meeting leader have it to start with).

## 🧪 A first test
- **A host:** sign in as an officer, open **Video meeting** and join. You're in at once, as the host (moderator).
- **A member:** another member joins the same room. They skip the lobby but don't host.
- **A guest:** in a private window, open `https://meet.example.org/club` without signing in. The lobby asks for a name, and the host gets a knock to let them in. Before a host is in, the guest waits.
- **The bare address:** `https://meet.example.org/` opens this app's Video meeting page.
- **Hanging up** shows Jitsi's thank-you page.

If a join says "Authentication failed", the app ID or secret differs between the two servers, or the two servers' clocks are more than 10 seconds apart.
