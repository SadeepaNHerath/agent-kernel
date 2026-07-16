# Telegram Test Script For Judges

Use this script after the server is running, the HTTPS tunnel is connected, and the webhook is set.

## 1. Confirm Bot Commands

In Telegram, open the bot chat and type `/`.

Expected commands:

- `/start`
- `/new_event`
- `/context`
- `/brief`
- `/pack`
- `/impact`
- `/report`
- `/dashboard`
- `/status`
- `/help`

## 2. Start The Product Flow

```text
/start
/new_event IDEALIZE AI Workshop
```

Expected result:

- Short human reply, not raw JSON.
- Event ID is shown.
- A `Show Status` button appears.

## 3. Add Event Style

```text
/context theme=modern tech, confident student tone colors=blue, white caption=Hook, details, CTA, hashtags sample_caption=Ready to build with AI? Register now. #IDEALIZE #AIWorkshop
```

Expected result:

- Bot confirms style was updated.
- It shows saved colors/hashtags.

## 4. Upload Sample Flyer

Upload a sample flyer image or PDF and use this Telegram caption:

```text
sample for ck-idealize-ai-workshop
```

Upload a second sample with the same caption to show that CampaignKernel combines patterns across samples.

Expected result:

- Bot confirms the sample was saved.
- Bot summarizes sample count, layout, pattern, style, and palette.

## 5. Create Campaign Brief

```text
/brief Free AI workshop for university students on July 25 at University of Moratuwa. Register via link in bio.
```

Expected result:

- Bot shows title, date, venue, and CTA.
- Buttons appear: `Approve Content`, `One-Click Pack`, `Edit Content`, `Status`.

## 6. One-Click Winning Demo

Click:

- `One-Click Pack`

Expected result:

- Bot sends the generated flyer PNG directly in chat.
- Bot shows SDG alignment and quality score.
- Bot shows the caption preview.
- Flyer includes a visible SDG badge.

Optional command version:

```text
/pack premium SDG campaign, larger CTA
```

## 7. Manual Approval Flow

Click:

- `Approve Content`
- `Generate Flyer`

Expected result:

- Bot sends the generated flyer PNG directly in chat.
- Flyer message includes `Approve Flyer`, `Edit Flyer`, and `Regenerate` buttons.

Optional edit test:

```text
/generate_flyer dark bold centered poster with large title and big CTA
```

Expected result:

- The new flyer visually changes layout and emphasis.

## 8. Captions, Impact, And Final Approval

Click:

- `Approve Flyer`
- `Generate Captions`
- `Approve Caption`
- `Approve Campaign`

Expected result:

- Bot shows a short Instagram caption preview.
- `/impact` shows SDGs, impact goals, best CTA, accessibility, and quality score.
- `/report` creates a Markdown impact report path for judges.
- `/dashboard` shows aggregate campaign metrics.
- Final approval shows publish/export buttons.

Optional caption edit test:

```text
/edit make it shorter and more professional, no hashtags
```

Expected result:

- Caption text is rewritten, shorter, and hashtags are removed.

## 9. Publish Demo

Click:

- `Publish IG/FB/LinkedIn`

Expected result with `CAMPAIGN_KERNEL_MOCK_PUBLISH=true`:

- Bot shows mock publish results for Instagram, Facebook, and LinkedIn.

For WhatsApp, click:

- `WhatsApp Export`

Expected result:

- Bot returns a WhatsApp-ready export message.

## 10. Web Workspace Check

Open:

```text
http://127.0.0.1:8000/
http://127.0.0.1:8000/campaign/docs
```

Expected result:

- Web workspace loads.
- Generate Pack creates flyer preview, captions, SDG chips, impact metrics, and report link.
- Documentation page explains the architecture and demo flow.

## 11. Debug Only If Needed

Use this only during development:

```text
/debug_status
```

Expected result:

- Raw campaign JSON for checking state.
