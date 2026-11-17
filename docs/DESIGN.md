# Design notes

Principles (inspiration only, no code or assets copied):
- One metric or a close group per card; actions out of the way
  ([PatternFly dashboard guidelines](https://patternfly.org/patterns/dashboard/design-guidelines)).
- Data-dense lists as tables, not cards ([Actabl card guidance](https://design.actabl.com/components/card)).
- Dark mode cards a step lighter than the page ([Appian SAIL card layout](https://docs.appian.com/suite/help/25.4/sail/ux-card-layout.html)).
- System font stack (no web fonts), tabular numbers, 4.5:1 text contrast, visible focus,
  `prefers-reduced-motion` honoured, charts with text alternatives.
- What an admin can do comes from Ente's docs ([administration](https://ente.com/help/self-hosting/administration/users))
  and museum's admin API (`server/pkg/api/admin.go` upstream).

Colours: green/teal for usage, amber above 85%, red above 95% (each with a text label, so
colour is never the only signal).
