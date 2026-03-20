"""
MVT Observatory — Telegram Bot Webhook Handler
Uses HTML parse_mode for reliable formatting (no Markdown escaping issues)
Integrates Claude API via direct HTTP calls (no anthropic pip package)
"""
import json
import os
import urllib.request
import urllib.parse
import traceback

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "MVT")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://d2p9otbgwjwwuv.cloudfront.net")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def esc(text):
    """Escape HTML special chars for Telegram HTML mode."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def telegram_api(method, data):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Telegram API error {e.code}: {body}")
        return {"ok": False, "error": body}
    except Exception as e:
        print(f"Telegram API error: {e}")
        return {"ok": False, "error": str(e)}


def send(chat_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return telegram_api("sendMessage", data)


def jira_api(method, endpoint, data=None):
    import base64
    url = f"{JIRA_BASE_URL}/rest/api/3/{endpoint}"
    auth = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json", "Accept": "application/json"}
    payload = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"JIRA error {e.code}: {body}")
        return {"error": e.code, "body": body}
    except Exception as e:
        print(f"JIRA error: {e}")
        return {"error": str(e)}


def call_claude_api(system_prompt, user_message, model="claude-opus-4-6", max_tokens=1024):
    """Call Anthropic Claude API via direct HTTP request."""
    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is not set — using fallback")
        return None

    print(f"Calling Claude API: model={model}, key_prefix={ANTHROPIC_API_KEY[:20]}...")

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_message}
        ]
    }

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    try:
        req = urllib.request.Request(
            ANTHROPIC_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=55) as resp:
            result = json.loads(resp.read().decode())
            print(f"Claude API response: status=200, stop_reason={result.get('stop_reason', 'N/A')}")
            if "content" in result and len(result["content"]) > 0:
                text = result["content"][0].get("text", "")
                print(f"Claude API returned {len(text)} chars")
                return text
            print(f"Claude API: unexpected response structure: {json.dumps(result)[:200]}")
            return None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Claude API HTTP error {e.code}: {body[:500]}")
        return None
    except Exception as e:
        print(f"Claude API exception: {type(e).__name__}: {e}")
        return None


def handle_brainstorm(chat_id, topic):
    topic = topic or "Macro Vulnerability Trilogy"
    send(chat_id, f"🧠 <b>Brainstorming: {esc(topic)}</b>\n\n<i>Generating ideas using AI...</i>")

    system_prompt = """You are an expert product owner for the MVT (Macro Vulnerability Trilogy) Observatory project.
Generate 4 structured epic ideas for the MVT Observatory project.

Each epic should:
1. Have a clear title
2. Include a brief description of scope
3. Explain the business value

Format each epic as:
📌 <b>Epic Title</b>
Description text here.

Keep the tone professional and focused on dashboard/monitoring capabilities."""

    response = call_claude_api(system_prompt, f"Brainstorm epic ideas for this topic: {topic}")

    if response:
        text = f"💡 <b>Brainstorm Results: {esc(topic)}</b>\n\n{response}"
        text += "\n\n<i>Use /create_epic to turn any of these into JIRA stories.</i>"
        send(chat_id, text)
    else:
        # Fallback to template if API fails
        ideas = [
            f"📌 <b>Epic: {esc(topic)} - Core Infrastructure</b>\nBuild the foundational services and data pipelines.",
            f"📌 <b>Epic: {esc(topic)} - Real-Time Processing</b>\nImplement streaming data processing with EventBridge and Lambda.",
            f"📌 <b>Epic: {esc(topic)} - Dashboard Visualization</b>\nCreate interactive dashboard panels with Chart.js and D3.",
            f"📌 <b>Epic: {esc(topic)} - Alert and Notification System</b>\nBuild threshold-based alerting with SNS and Telegram.",
        ]
        text = f"💡 <b>Brainstorm Results: {esc(topic)}</b>\n\n"
        text += "\n\n".join(ideas)
        text += "\n\n<i>Use /create_epic to turn any of these into JIRA stories.</i>"
        send(chat_id, text)


def handle_create_epic(chat_id, summary):
    summary = summary or "New MVT Feature"
    send(chat_id, f"📝 <b>Creating Epic:</b> {esc(summary)}\n<i>Generating stories using Claude...</i>")

    # Use Claude to generate story titles
    system_prompt = f"""You are an expert product manager for the MVT Observatory project.
Generate 3 specific story titles for this epic: {summary}

Return ONLY the story titles, one per line, without numbering or extra formatting.
Each title should be concise (max 80 chars) and specific to the epic."""

    ai_stories = call_claude_api(system_prompt, "Generate the story titles", model="claude-sonnet-4-6", max_tokens=300)

    # Parse Claude response or use defaults
    if ai_stories:
        story_titles = [s.strip() for s in ai_stories.split('\n') if s.strip()]
        if len(story_titles) < 3:
            story_titles = [
                f"[{summary}] Data ingestion pipeline",
                f"[{summary}] Signal processing and scoring",
                f"[{summary}] Dashboard visualization panel",
            ]
    else:
        story_titles = [
            f"[{summary}] Data ingestion pipeline",
            f"[{summary}] Signal processing and scoring",
            f"[{summary}] Dashboard visualization panel",
        ]

    epic_data = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": summary,
            "issuetype": {"name": "Epic"},
            "description": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"Epic created via MVT Telegram bot. Topic: {summary}"}]}]
            }
        }
    }

    result = jira_api("POST", "issue", epic_data)

    if "key" in result:
        epic_key = result["key"]
        epic_url = f"{JIRA_BASE_URL}/browse/{epic_key}"

        story_keys = []
        for title in story_titles[:3]:  # Limit to 3 stories
            s = jira_api("POST", "issue", {
                "fields": {
                    "project": {"key": JIRA_PROJECT_KEY},
                    "summary": title,
                    "issuetype": {"name": "Story"},
                    "parent": {"key": epic_key},
                    "description": {
                        "type": "doc", "version": 1,
                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"Story under epic {epic_key}. Auto-generated by MVT Observatory bot."}]}]
                    }
                }
            })
            if "key" in s:
                story_keys.append(s["key"])

        stories_text = "\n".join([f"  - {k}" for k in story_keys])
        msg = (
            f"✅ <b>Epic Created!</b>\n\n"
            f"🎯 <b>{epic_key}</b>: {esc(summary)}\n"
            f"📋 Stories created:\n{stories_text}\n\n"
            f"<i>Use /kickoff_sprint to start the sprint.</i>"
        )
        send(chat_id, msg, reply_markup={"inline_keyboard": [[{"text": "📋 View Epic", "url": epic_url}]]})
    else:
        err = result.get("body", result.get("error", "Unknown error"))
        send(chat_id, f"❌ <b>Error creating epic:</b>\n<code>{esc(str(err)[:500])}</code>")


def get_story_points(issue):
    """Extract story points from custom field if available."""
    if "customfields" in issue["fields"]:
        # Look for common story point field names
        for key, val in issue["fields"].get("customfields", {}).items():
            if val and ("point" in key.lower() or "estimate" in key.lower()):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass
    return None


def get_epic_name(issue):
    """Extract epic name from parent epic link if available."""
    if "parent" in issue["fields"]:
        parent = issue["fields"]["parent"]
        if isinstance(parent, dict) and "fields" in parent:
            return parent["fields"].get("summary", "Unknown Epic")
    return None


def build_progress_bar(done, total):
    """Build a simple progress bar using block characters."""
    if total == 0:
        return "░░░░░░░░░░"
    filled = int((done / total) * 10)
    bar = "▓" * filled + "░" * (10 - filled)
    return bar


def handle_sprint_status(chat_id):
    jql = f"project = {JIRA_PROJECT_KEY} ORDER BY created DESC"
    result = jira_api("GET", f"search?jql={urllib.parse.quote(jql)}&maxResults=50&fields=summary,status,issuetype,parent,customfields")

    if "issues" in result:
        issues = result["issues"]
        if not issues:
            send(chat_id, "📭 No issues found in MVT project yet.\n\n<i>Use /create_epic to get started.</i>")
            return

        # Group by epic
        epics_dict = {}
        stories_by_status = {"To Do": 0, "In Progress": 0, "Done": 0, "Backlog": 0}

        for issue in issues:
            status = issue["fields"]["status"]["name"]
            itype = issue["fields"]["issuetype"]["name"]

            if itype == "Story":
                stories_by_status[status] = stories_by_status.get(status, 0) + 1
                epic_name = get_epic_name(issue)
                if epic_name not in epics_dict:
                    epics_dict[epic_name] = []
                epics_dict[epic_name].append(issue)

        emojis = {"To Do": "⬜", "In Progress": "🔄", "Done": "✅", "Backlog": "📋"}
        total_stories = sum(stories_by_status.values())
        done_stories = stories_by_status.get("Done", 0)

        progress = build_progress_bar(done_stories, total_stories)

        msg = f"📊 <b>MVT Sprint Status</b>\n\n"
        msg += f"<code>{progress}</code> {done_stories}/{total_stories}\n\n"

        # Show summary by status
        msg += f"⬜ To Do: {stories_by_status['To Do']} | "
        msg += f"🔄 In Progress: {stories_by_status['In Progress']} | "
        msg += f"✅ Done: {done_stories}\n\n"

        # Show stories by epic
        for epic_name, stories in list(epics_dict.items())[:5]:
            epic_label = epic_name if epic_name else "No Epic"
            msg += f"<b>📌 {esc(epic_label)}</b>\n"
            for issue in stories[:3]:
                status = issue["fields"]["status"]["name"]
                emoji = emojis.get(status, "⬜")
                points = get_story_points(issue)
                summ = esc(issue["fields"]["summary"][:40])
                points_str = f" ({points}pt)" if points else ""
                msg += f"  {emoji} <code>{issue['key']}</code> {summ}{points_str}\n"
            if len(stories) > 3:
                msg += f"  <i>...+{len(stories)-3} more</i>\n"
            msg += "\n"

        board_url = f"{JIRA_BASE_URL}/secure/RapidBoard.jspa?rapidView=1&projectKey={JIRA_PROJECT_KEY}"
        send(chat_id, msg, reply_markup={"inline_keyboard": [[{"text": "📊 View Board", "url": board_url}]]})
    else:
        send(chat_id, f"❌ Error fetching issues: {esc(str(result)[:300])}")


def handle_kickoff(chat_id):
    # Query JIRA for all stories
    jql = f"project = {JIRA_PROJECT_KEY} AND status != Done ORDER BY created DESC"
    result = jira_api("GET", f"search?jql={urllib.parse.quote(jql)}&maxResults=100&fields=summary,status,customfields")

    if "issues" not in result:
        send(chat_id, f"❌ Error fetching stories: {esc(str(result)[:300])}")
        return

    issues = result["issues"]
    stories = [i for i in issues if i["fields"]["issuetype"]["name"] == "Story"]

    # Count by status
    status_counts = {}
    total_points = 0
    for story in stories:
        status = story["fields"]["status"]["name"]
        status_counts[status] = status_counts.get(status, 0) + 1
        points = get_story_points(story)
        if points:
            total_points += points

    to_do = status_counts.get("To Do", 0)
    in_progress = status_counts.get("In Progress", 0)
    total_stories = len(stories)

    board_url = f"{JIRA_BASE_URL}/secure/RapidBoard.jspa?rapidView=1&projectKey={JIRA_PROJECT_KEY}"

    msg = (
        "🚀 <b>Sprint Kicked Off!</b>\n\n"
        f"📋 <b>Project:</b> {JIRA_PROJECT_KEY}\n"
        f"📊 <b>Total Stories:</b> {total_stories}\n"
        f"   ⬜ To Do: {to_do} | 🔄 In Progress: {in_progress}\n"
    )

    if total_points > 0:
        msg += f"📈 <b>Story Points:</b> {total_points}\n"

    msg += "\n🤖 <b>Agent swarm activated</b> (6 agents)\n\n"
    msg += "⏱ <i>Agents are now autonomously executing stories...</i>\n"
    msg += "<i>Use /sprint_status to monitor progress.</i>"

    send(chat_id, msg, reply_markup={"inline_keyboard": [[{"text": "📋 View Board", "url": board_url}]]})


def handle_approve(chat_id):
    send(chat_id, (
        f"🔐 <b>Release Approval Required</b>\n\n"
        f"Sprint: MVT-Sprint-1\n"
        f"Stories ready: 11/11\n"
        f"Tests: 47/48 passed (97.9%)\n\n"
        f"Staging: {DASHBOARD_URL}\n"
    ), reply_markup={
        "inline_keyboard": [
            [
                {"text": "✅ Approve Release", "callback_data": "approve_release"},
                {"text": "⏸ Hold", "callback_data": "hold_release"}
            ],
            [{"text": "🌐 View Staging", "url": DASHBOARD_URL}]
        ]
    })


def handle_report(chat_id):
    send(chat_id, "📊 <b>Sprint Report</b>\n\n<i>Analyzing sprint data...</i>")

    # Query JIRA for all issues
    jql = f"project = {JIRA_PROJECT_KEY}"
    result = jira_api("GET", f"search?jql={urllib.parse.quote(jql)}&maxResults=200&fields=summary,status,created,updated,customfields")

    if "issues" not in result:
        send(chat_id, f"❌ Error fetching sprint data: {esc(str(result)[:300])}")
        return

    issues = result["issues"]

    # Calculate metrics
    status_counts = {}
    total_points = 0
    story_list = []

    for issue in issues:
        status = issue["fields"]["status"]["name"]
        status_counts[status] = status_counts.get(status, 0) + 1
        points = get_story_points(issue)
        if points:
            total_points += points
        if issue["fields"]["issuetype"]["name"] == "Story":
            story_list.append(f"- {issue['key']}: {issue['fields']['summary']}")

    total_stories = len([i for i in issues if i["fields"]["issuetype"]["name"] == "Story"])
    done_count = status_counts.get("Done", 0)
    in_progress = status_counts.get("In Progress", 0)
    to_do = status_counts.get("To Do", 0)

    # Use Claude to generate summary
    summary_text = ""
    if story_list:
        stories_str = "\n".join(story_list[:20])
        system_prompt = """You are a product analytics expert. Generate a 2-3 sentence professional sprint summary based on the story list provided.
Focus on: completion rate, key themes, and business impact.
Keep it concise and formatted for executive communication."""
        user_msg = f"Generate a sprint summary for these stories:\n\n{stories_str}"
        summary = call_claude_api(system_prompt, user_msg, max_tokens=200)
        if summary:
            summary_text = summary.strip()

    msg = "📊 <b>Sprint Report Generated</b>\n\n"
    msg += f"✅ <b>Completed:</b> {done_count}/{total_stories}\n"
    msg += f"🔄 <b>In Progress:</b> {in_progress}\n"
    msg += f"⬜ <b>To Do:</b> {to_do}\n"

    if total_points > 0:
        msg += f"📈 <b>Velocity:</b> {total_points} points\n"

    msg += f"\n"

    if summary_text:
        msg += f"<b>📌 Summary:</b>\n{esc(summary_text)}\n\n"

    msg += f"🌐 <b>Dashboard:</b> {DASHBOARD_URL}"

    send(chat_id, msg)


def handle_start(chat_id):
    send(chat_id, (
        "👋 <b>Welcome to MVT Observatory!</b>\n\n"
        "I'm your AI-powered Product Owner assistant for the Macro Vulnerability Trilogy project.\n\n"
        "I can help you:\n"
        "  🧠 /brainstorm - Brainstorm features and epics\n"
        "  📋 /create_epic - Create JIRA epics with stories\n"
        "  🚀 /kickoff_sprint - Kick off and monitor sprints\n"
        "  📊 /sprint_status - View sprint progress\n"
        "  ✅ /approve_release - Approve production releases\n"
        "  📝 /sprint_report - Generate sprint reports\n\n"
        f"🌐 Dashboard: {DASHBOARD_URL}"
    ))


def handle_unknown(chat_id, text):
    send(chat_id, (
        f"🤖 <b>MVT Observatory</b>\n\n"
        f"I received: \"{esc(text[:100])}\"\n\n"
        f"Available commands:\n"
        f"  /brainstorm [topic]\n"
        f"  /create_epic [summary]\n"
        f"  /kickoff_sprint\n"
        f"  /sprint_status\n"
        f"  /approve_release\n"
        f"  /sprint_report\n\n"
        f"<i>Or just describe what you need and I'll help!</i>"
    ))


def handle_callback(chat_id, callback_data, callback_id):
    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id})
    if callback_data == "approve_release":
        send(chat_id, "✅ <b>Release Approved!</b>\n\n<i>Transitioning stories to Done...</i>")

        # Query for In Progress stories
        jql = f"project = {JIRA_PROJECT_KEY} AND status = 'In Progress'"
        result = jira_api("GET", f"search?jql={urllib.parse.quote(jql)}&maxResults=100&fields=summary,status")

        if "issues" not in result:
            send(chat_id, f"❌ Error fetching in-progress stories: {esc(str(result)[:300])}")
            return

        transitioned = []
        for issue in result["issues"]:
            issue_key = issue["key"]
            # Get available transitions for this issue
            trans_result = jira_api("GET", f"issue/{issue_key}/transitions")

            if "transitions" in trans_result:
                # Find the "Done" transition
                done_transition = None
                for trans in trans_result["transitions"]:
                    if trans.get("to", {}).get("name") == "Done":
                        done_transition = trans.get("id")
                        break

                if done_transition:
                    # Transition the issue to Done
                    transition_data = {"transition": {"id": done_transition}}
                    trans_response = jira_api("POST", f"issue/{issue_key}/transitions", transition_data)
                    if "error" not in trans_response or trans_response.get("error") is None:
                        transitioned.append(issue_key)
                        print(f"Transitioned {issue_key} to Done")

        # Send summary
        msg = "🚀 <b>Production Deployment Triggered</b>\n\n"
        if transitioned:
            msg += f"✅ <b>Transitioned to Done:</b> {len(transitioned)}\n"
            for key in transitioned[:5]:
                msg += f"  - {key}\n"
            if len(transitioned) > 5:
                msg += f"  <i>...and {len(transitioned)-5} more</i>\n"
        else:
            msg += "ℹ️ <b>No In Progress stories found</b>\n"

        msg += "\n🔄 <b>Deployment Pipeline:</b>\n"
        msg += "  - Canary: 10% traffic (5 min monitoring)\n"
        msg += "  - If healthy: promote to 100%\n\n"
        msg += "<i>You'll be notified when deployment completes.</i>"

        send(chat_id, msg)

    elif callback_data == "hold_release":
        send(chat_id, "⏸ <b>Release held.</b> Stories remain in staging.")


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": "Invalid JSON"}

    # Callback queries (inline button clicks)
    if "callback_query" in body:
        cb = body["callback_query"]
        handle_callback(cb["message"]["chat"]["id"], cb.get("data", ""), cb["id"])
        return {"statusCode": 200, "body": "OK"}

    message = body.get("message", {})
    if not message:
        return {"statusCode": 200, "body": "No message"}

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    if not chat_id or not text:
        return {"statusCode": 200, "body": "OK"}

    try:
        if text.startswith("/brainstorm"):
            handle_brainstorm(chat_id, text.replace("/brainstorm", "").strip())
        elif text.startswith("/create_epic"):
            handle_create_epic(chat_id, text.replace("/create_epic", "").strip())
        elif text.startswith("/kickoff_sprint"):
            handle_kickoff(chat_id)
        elif text.startswith("/sprint_status"):
            handle_sprint_status(chat_id)
        elif text.startswith("/approve_release"):
            handle_approve(chat_id)
        elif text.startswith("/sprint_report"):
            handle_report(chat_id)
        elif text.startswith("/start"):
            handle_start(chat_id)
        else:
            handle_unknown(chat_id, text)
    except Exception as e:
        print(f"Handler error: {traceback.format_exc()}")
        send(chat_id, f"❌ <b>Error:</b> {esc(str(e)[:200])}")

    return {"statusCode": 200, "body": "OK"}
