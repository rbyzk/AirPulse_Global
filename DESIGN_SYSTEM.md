# AirPulse Global Design System

## Purpose

This document defines the visual and interaction principles used across AirPulse Global. It is intended to keep the Streamlit application consistent as the dashboard, station map, analytics, forecast, and reporting pages evolve.

## Product Tone

AirPulse should feel:

- clear
- trustworthy
- calm
- analytical
- operational rather than decorative

The interface should prioritize comprehension over novelty. Visual emphasis should support environmental risk interpretation, not compete with it.

## Core UI Principles

### 1. Data First

Every visual element should help the user answer one of these questions:

- What is the air quality right now?
- How risky is it?
- What changed?
- What should I do next?

### 2. One Meaning Per Surface

The same label should not represent different calculations in different places. If a card says `Current AQI`, it should come from the same live-source logic across the app.

### 3. Explicit Source Transparency

Where relevant, the UI should distinguish between:

- live provider data
- fallback estimates
- historical backtests
- notebook or offline benchmarks

### 4. Calm Density

AirPulse contains many analytics surfaces, but each page should remain readable. Cards, charts, and maps should breathe, with enough white space to avoid cognitive overload.

## Color System

### Brand Colors

- Primary blue: `#007AFF`
- Secondary purple: `#5856D6`
- Accent violet: `#AF52DE`
- Success green: `#34C759`
- Warning yellow: `#FFCC00`
- Risk orange: `#FF9500`
- Danger red: `#FF3B30`

### Neutral Palette

- Background: `#F5F7FB`
- Card background: `#FFFFFF`
- Primary text: `#1D1D1F`
- Secondary text: `#6B7280`
- Tertiary text: `#8E8E93`
- Border: `#E5E7EB`

### AQI State Mapping

- Good: green
- Moderate: yellow
- Unhealthy for Sensitive Groups: orange
- Unhealthy: red
- Very Unhealthy: purple
- Hazardous: deep maroon

AQI status colors must remain semantically stable across cards, badges, charts, and map markers.

## Typography

### Font Family

- Primary UI font: `Inter`

### Hierarchy

- Hero titles: strong, bold, compact
- Section headers: short and scannable
- KPI values: large and high contrast
- Supporting text: muted and compact

Typography should stay simple and avoid decorative font mixing.

## Layout

### Card System

All major surfaces should use a card-based layout with:

- white background
- soft border or shadow
- generous padding
- consistent corner radius

### Spacing

Use a consistent spacing rhythm:

- tight: 8px
- standard: 16px
- section: 24px
- large section: 32px

### Grid Logic

- Dashboard metrics: 4-column desktop, stacked on small screens
- Technology and feature cards: symmetrical heights where possible
- Analytics: two-column analytical layout when charts are paired

## Page-Specific Guidance

### Dashboard

The dashboard should prioritize:

1. current AQI
2. PM2.5
3. wind context
4. map
5. city comparison

It should not feel like a notebook or research workspace.

### Global Station Map

The map should remain visually useful without becoming heavy:

- rely on WAQI tile density first
- avoid duplicate overlays when they do not add clear value
- keep captions short

### Forecast

The forecast page should show:

- latest observed value
- forecast mean
- peak risk
- WHO breach days
- forecast mode

Diagnostics should stay brief. Forecast transparency is helpful, but the page should not turn into a model debugging console.

### Analytics

Analytics is the only page allowed to be denser. Even there:

- each analysis lens should have a single analytical purpose
- empty states should be hidden when possible
- fallback behavior must be labeled clearly

### About

The About page should feel polished and presentation-ready:

- clean hero
- symmetrical cards
- no broken encoding
- no placeholder labels
- badges should be compact, not full-width bars

## Components

### KPI Cards

Each KPI card should contain:

- short uppercase label
- large value
- short descriptor or unit

Avoid long paragraphs inside KPI cards.

### Status Badges

Badges should be:

- compact
- rounded
- color-coded
- semantically stable

Examples:

- `LIVE`
- `Provider`
- `Fallback`
- `Good`
- `Moderate`

### Charts

Charts should use Plotly with:

- light background
- muted gridlines
- minimal toolbar noise
- no unnecessary modebar when interaction is not essential

### Maps

Maps should:

- load reliably
- use restrained controls
- avoid duplicate attribution noise where legally safe and technically appropriate
- prefer clarity over excessive layers

## Performance Guidance

Design and performance are linked. In AirPulse:

- repeated live calls should be cached
- map overlays should be minimized
- visual duplication should be avoided
- heavy analytics should only load when the user asks for them

If a design choice noticeably slows the page without adding strong user value, simplify it.

## Accessibility

AirPulse should aim for:

- strong text contrast
- readable font sizes
- keyboard-friendly interaction where Streamlit allows it
- meaning not conveyed by color alone

## Documentation Rule

When UI behavior changes materially, this file and the main [README.md](./README.md) should be kept aligned.
