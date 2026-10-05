# Third-party design attribution

The DLA React application adapts the console layout, dark/light semantic colors, left navigation rail, context strip, and docked assistant interaction pattern from [VA Clinic Scheduling](https://github.com/lucas-torlay-datarobot/va-clinic-scheduling), reference commit `e787cf5a4376e3e89a63c084212c528b6a0e9d6c`:

- `frontend_web/src/components/shared/AppShell.tsx`
- `frontend_web/src/components/shared/AgentPanel.tsx`
- `frontend_web/src/theme/demo-theme.css`

The DLA components, domain semantics, query execution service, memory store and data preparation are adapted/new implementations rather than the full VA infrastructure scaffold. The upstream Apache 2.0 license is retained at `licenses/va-clinic-scheduling-Apache-2.0.txt`. This demonstration does not imply endorsement by DLA or VA.
