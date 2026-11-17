-- Club Minutes' rules for guests in a room, carried in members' tokens as context.room.club:
--   guests: false = members only (a guest is refused, not even let knock)
--   guest_mic, guest_camera, guest_screen, guest_chat: false = guests may not, until a host allows it
--   last_host_leaves: "end" (end the meeting), "guests" (drop the guests) or "stay"
-- A guest is anyone without a token. Mic, camera and screen use Jitsi's own AV moderation (enforced by Jicofo);
-- members (token holders) are put on its allow list as they join, so only guests have to ask.
-- Guests are refused (fail closed) where their rules can't be applied: no member in the room yet to set them, or
-- Jitsi's AV moderation component missing.
-- Load on the main MUC component, with av_moderation_component set up as usual.
local util = module:require 'util';
local is_healthcheck_room, is_admin, is_focus = util.is_healthcheck_room, util.is_admin, util.is_focus;
local get_room_from_jid = util.get_room_from_jid;
local internal_room_jid_match_rewrite = util.internal_room_jid_match_rewrite;
local st = require 'util.stanza';

local MEDIA = { mic = 'audio', camera = 'video', screen = 'desktop' };
local GRACE = module:get_option_number('club_rules_host_grace', 60); -- seconds a host may drop out and come back
local base = module:get_option_string('muc_mapper_domain_base') or module.host:gsub('^[^.]+%.', '');
local av_host = module:get_option_string('av_moderation_component', 'avmoderation.' .. base);

local function av()
    local h = prosody.hosts[av_host];
    return h and h.modules.av_moderation_component;
end

local function rules(room)
    return room._data.club_rules;
end

module:hook('muc-occupant-joined', function (event)
    local room, occupant, session = event.room, event.occupant, event.origin;
    if is_healthcheck_room(room.jid) or is_admin(occupant.bare_jid) or not (session and session.auth_token) then
        return;
    end
    local ctx = session.jitsi_meet_context_room;
    if not rules(room) and type(ctx) == 'table' and type(ctx.club) == 'table' then
        room._data.club_rules = ctx.club; -- the first member in sets the room's rules
    end
    local r = rules(room);
    if not r then return; end
    local mod = av();
    if not mod then
        module:log('error', 'no %s: guests are refused where they would not be held back', av_host);
        return;
    end
    local me = internal_room_jid_match_rewrite(occupant.nick);
    for key, media in pairs(MEDIA) do
        if r['guest_' .. key] == false then
            if not (room.av_moderation and room.av_moderation[media]) then
                mod.start_av_moderation(room, media, occupant);
                mod.notify_occupants_enable(nil, true, room, occupant.nick, media);
            end
            if not mod.get_index_in_table(room.av_moderation[media], me) then
                room.av_moderation[media]:push(me);
            end
            mod.notify_whitelist_change(occupant.jid, true, room, media, false);
        end
    end
end, -3); -- after av_moderation's own join handling (-2)

local function needs_av(r)
    return r.guest_mic == false or r.guest_camera == false or r.guest_screen == false;
end

-- Fails closed: a guest is refused unless the room's rules are known (a member came first) and can be applied.
module:hook('muc-occupant-pre-join', function (event)
    local room, session = event.room, event.origin;
    if not session or session.auth_token or is_admin(event.occupant.bare_jid) or is_healthcheck_room(room.jid) then
        return;
    end
    local r, why = rules(room), nil;
    if not r then
        why = 'This meeting has not started: a member must join first.';
    elseif r.guests == false then
        why = 'This meeting is for members only.';
    elseif needs_av(r) and not av() then
        module:log('error', 'no %s: refusing a guest in %s', av_host, room.jid);
        why = 'This meeting cannot admit guests right now.';
    end
    if why then
        session.send(st.error_reply(event.stanza, 'cancel', 'not-allowed', why));
        return true;
    end
end, -3.9); -- before the lobby (-4) sends them to knock

local function on_message(event)
    local stanza, session = event.stanza, event.origin;
    if not stanza:get_child('body') or not session or session.auth_token then return; end
    local room = get_room_from_jid(stanza.attr.to);
    if room and rules(room) and rules(room).guest_chat == false then
        module:send(st.error_reply(stanza, 'cancel', 'not-allowed', 'Guests may not chat in this meeting'));
        return true;
    end
end
module:hook('message/bare', on_message, 1); -- the room's chat
module:hook('message/full', on_message, 1); -- private messages to someone in the room

local function has_host(room)
    for _, o in room:each_occupant() do
        if o.role == 'moderator' and not is_focus(o.nick) then return true; end
    end
end

module:hook('muc-occupant-left', function (event)
    local room = event.room;
    local r = rules(room);
    if not r or (r.last_host_leaves ~= 'end' and r.last_host_leaves ~= 'guests') or has_host(room) then return; end
    module:add_timer(GRACE, function ()
        room = get_room_from_jid(room.jid);
        if not room or has_host(room) then return; end
        if r.last_host_leaves == 'end' then
            module:log('info', 'last host left %s: ending it', room.jid);
            room:destroy(nil, 'The meeting ended: its host left.');
            return;
        end
        for _, o in room:each_occupant() do
            local s = prosody.full_sessions[o.jid];
            if not is_focus(o.nick) and not (s and s.auth_token) then
                room:set_role(true, o.nick, 'none', 'The host left.');
            end
        end
    end);
end);
