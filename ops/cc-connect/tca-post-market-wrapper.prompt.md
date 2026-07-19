# Trading-Copilot Post-Market Skill Trigger

Use repo-only Skill `$tca-post-market-review` to run the scheduled Trading-Copilot-Agent post-market workflow from repository root.

Follow that Skill's complete analysis contract and scheduled cc-connect handoff. Do not reproduce or replace its workflow from this Prompt.

The outer shell owns Feishu send and mark-sent. Do not call `cc-connect send`; keep Longbridge real-account access read-only and do not place trades.

End with `FEISHU_SUMMARY_READY` after the Skill has written its summary and delivery metadata artifacts.
