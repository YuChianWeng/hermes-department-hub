/**
 * Department Hub — Desktop management page (public @hermes/plugin-sdk only).
 *
 * What this half does:
 *  - registers a full page at /department-hub and a sidebar nav row;
 *  - lists configured departments with manager / members / reviewers and the
 *    live work state of their bound Kanban cards;
 *  - shows, per task: Kanban status, last activity, blocking reason, the
 *    independent review verdict (run id / reviewer / artifact SHA) and the
 *    delivered final report committed in the plugin's own SQLite
 *    (``deliveries.body_json``; summary shown as escaped plain text);
 *  - lists the department's delivered reports independently of the board, so a
 *    historical report stays readable if the board later fails or rejects a new
 *    generation;
 *  - offers department creation as an explicitly blocked action: the form is
 *    validated by the backend, then the backend refuses with a stable code and
 *    prints the operator steps. This page never claims it can write config.
 *
 * Truth rules this file follows:
 *  - every value rendered comes from the backend payload; nothing is inferred
 *    or filled in locally;
 *  - status is always text (never colour alone);
 *  - a read failure is shown as a failure, never as an empty department list;
 *  - the page states it is read-only and that a profile is not a sandbox.
 *
 * Rendering itself is NOT verified by unit tests — the SDK-mock test exercises
 * component logic with stubbed hooks. A real Desktop screenshot is separate
 * acceptance.
 */

import {
  host,
  useQuery,
  usePluginI18n,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  PALETTE_AREA,
  Button,
  Input,
  EmptyState,
  ErrorState,
  Skeleton,
  Separator,
  cn,
  relativeTime
} from '@hermes/plugin-sdk'
import * as React from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'department-hub'
const PAGE_PATH = '/department-hub'
const REFRESH_MS = 10000

// ── formatting helpers ──────────────────────────────────────────────────────

function ageLabel(seconds, t) {
  if (typeof seconds !== 'number' || !isFinite(seconds)) return t('never')
  return relativeTime(seconds * 1000)
}

function whenLabel(seconds, t) {
  if (typeof seconds !== 'number' || !isFinite(seconds)) return t('never')
  try {
    return new Date(seconds * 1000).toLocaleString()
  } catch (_err) {
    return t('never')
  }
}

function shortSha(value) {
  return typeof value === 'string' && value.length >= 12 ? `${value.slice(0, 12)}…` : value || ''
}

function statusKey(status) {
  return `status.${String(status || 'unknown')}`
}

// ── small primitives (text always present; colour is never the only signal) ──

function Tag({ children, title }) {
  return jsx('span', {
    title,
    className:
      'inline-flex items-center rounded border border-(--ui-stroke-secondary) px-1.5 py-0.5 ' +
      'text-[0.6875rem] leading-4 text-(--ui-text-secondary)',
    children
  })
}

function Field({ label, children }) {
  return jsxs('div', {
    className: 'flex min-w-0 flex-col gap-0.5',
    children: [
      jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: label }),
      jsx('span', { className: 'truncate text-xs text-(--ui-text-secondary)', children })
    ]
  })
}

function StatusBadge({ status, t }) {
  const label = t(statusKey(status), status)
  return jsx(Tag, { title: t('statusTitle', status), children: label })
}

// Manager health is three separate facts, never one green light: the public
// board can show a subscription, but cannot attest the runtime session owner,
// and a read-back subscription is not proof that the manager woke.
function ManagerStatus({ status, t }) {
  if (!status) return null
  const sub = status.subscription || {}
  const owner = status.owner || {}
  const wake = status.wake || {}
  return jsxs('div', {
    className: 'grid grid-cols-1 gap-2 md:grid-cols-3',
    role: 'group',
    'aria-label': t('managerStatusLabel'),
    children: [
      jsx(Field, {
        label: t('subscriptionLabel'),
        children: t(`subscription.${sub.state || 'unknown'}`, String(sub.matching || 0), String(sub.foreign || 0))
      }),
      jsx(Field, {
        label: t('ownerLabel'),
        children: t(`owner.${owner.state || 'unverified'}`, owner.code || '')
      }),
      jsx(Field, {
        label: t('wakeLabel'),
        children: t(`wake.${wake.state || 'unconfirmed'}`, wake.code || '')
      })
    ]
  })
}

// ── department list (keyboard: listbox with arrow keys) ─────────────────────

function DepartmentRow({ dept, selected, onSelect, onKeyDown, t }) {
  const blocked = dept.work && dept.work.blocked ? dept.work.blocked : 0
  const review = dept.work && dept.work.awaiting_review ? dept.work.awaiting_review : 0
  const held = dept.work && dept.work.review_held ? dept.work.review_held : 0
  const workState = (dept.work && dept.work.state) || 'unknown'
  const reportState = (dept.final_report && dept.final_report.state) || 'none'

  return jsx('div', {
    role: 'option',
    id: `department-hub-option-${dept.id}`,
    'aria-selected': selected ? 'true' : 'false',
    tabIndex: selected ? 0 : -1,
    'data-department': dept.id,
    className: cn(
      'flex w-full cursor-pointer flex-col gap-1 rounded px-2 py-1.5 text-left',
      selected ? 'bg-(--chrome-action-hover)' : 'hover:bg-(--chrome-action-hover)'
    ),
    onClick: () => onSelect(dept.id),
    onKeyDown,
    onFocus: () => onSelect(dept.id),
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between gap-2',
        children: [
          jsx('span', { className: 'truncate text-sm font-medium', children: dept.name }),
          jsx(StatusBadge, { status: workState, t })
        ]
      }),
      jsx('div', {
        className: 'truncate text-[0.6875rem] text-(--ui-text-tertiary)',
        children: t('managerLine', dept.manager)
      }),
      jsxs('div', {
        className: 'flex flex-wrap gap-1 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: [
          blocked ? jsx(Tag, { children: t('blockedCount', String(blocked)) }) : null,
          review ? jsx(Tag, { children: t('reviewCount', String(review)) }) : null,
          held ? jsx(Tag, { children: t('heldCount', String(held)) }) : null,
          jsx(Tag, {
            children:
              reportState === 'delivered'
                ? t('reportDelivered')
                : reportState === 'unknown'
                  ? t('reportUnknownShort')
                  : t('reportNotDelivered')
          }),
          jsx('span', { children: t('updatedAgo', ageLabel(dept.work && dept.work.last_activity_at, t)) })
        ]
      })
    ]
  })
}

function DepartmentList({ departments, selectedId, onSelect, t }) {
  const ids = departments.map(d => d.id)
  const move = delta => {
    if (ids.length === 0) return
    const at = Math.max(0, ids.indexOf(selectedId))
    const next = (at + delta + ids.length) % ids.length
    onSelect(ids[next])
    const node = document.getElementById(`department-hub-option-${ids[next]}`)
    if (node && typeof node.focus === 'function') node.focus()
  }
  const onKeyDown = event => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      move(1)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      move(-1)
    } else if (event.key === 'Home') {
      event.preventDefault()
      if (ids.length) onSelect(ids[0])
    } else if (event.key === 'End') {
      event.preventDefault()
      if (ids.length) onSelect(ids[ids.length - 1])
    }
  }

  const items = departments.map(dept =>
    jsx(
      DepartmentRow,
      { dept, selected: dept.id === selectedId, onSelect, onKeyDown, t },
      dept.id
    )
  )

  return jsx('div', {
    role: 'listbox',
    'aria-label': t('departmentListLabel'),
    'aria-activedescendant': selectedId ? `department-hub-option-${selectedId}` : undefined,
    className: 'flex flex-col gap-0.5',
    children: items
  })
}

// ── task evidence ───────────────────────────────────────────────────────────

// The final report is the plugin-persisted v2 body
// (``deliveries.body_json``). Its summary is untrusted plain text: it is
// rendered as a React text child (auto-escaped), never as HTML and never via
// dangerouslySetInnerHTML. It stays visible even if the board later becomes
// unavailable or a new generation is rejected.
function ReportSummary({ report, reportState, reportCode, t }) {
  if (reportState === 'report_too_large' || reportCode === 'report_too_large') {
    return jsx('div', {
      role: 'status',
      className: 'text-xs text-(--ui-text-secondary)',
      children: t('reportTooLarge')
    })
  }
  if (reportState === 'legacy' || reportState === 'not_confirmed') {
    return jsx('div', {
      className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
      children: t('reportLegacy')
    })
  }
  if (!report) return null
  return jsxs('div', {
    className: 'flex flex-col gap-0.5 rounded border border-(--ui-stroke-secondary) px-2 py-1',
    children: [
      jsx('div', { className: 'text-[0.6875rem] font-medium', children: t('deliveredSummaryLabel') }),
      jsx('div', {
        className: 'whitespace-pre-wrap break-words text-xs text-(--ui-text-secondary)',
        children: report.summary
      }),
      jsxs('div', {
        className: 'flex flex-wrap gap-x-3 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: [
          jsx('code', { children: report.schema || '' }),
          report.generation !== null && report.generation !== undefined
            ? jsx('span', { children: t('reportGeneration', String(report.generation)) })
            : null,
          report.size_bytes !== null && report.size_bytes !== undefined
            ? jsx('span', { children: t('reportBytes', String(report.size_bytes)) })
            : null
        ]
      })
    ]
  })
}

function DeliveredReports({ reports, t }) {
  if (!reports || reports.length === 0) return null
  return jsxs('section', {
    'aria-label': t('deliveredHeading'),
    className: 'flex flex-col gap-1 rounded border border-(--ui-stroke-secondary) px-2 py-1.5',
    children: [
      jsx('h3', { className: 'text-xs font-semibold', children: t('deliveredHeading') }),
      jsx('div', {
        className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
        children: t('deliveredSubheading')
      }),
      jsx('ul', {
        className: 'flex flex-col gap-1',
        children: reports.map(item =>
          jsxs(
            'li',
            {
              'data-delivered-task': item.task_id,
              className: 'flex flex-col gap-0.5 border-t border-(--ui-stroke-secondary) pt-1 first:border-t-0 first:pt-0',
              children: [
                jsxs('div', {
                  className: 'flex flex-wrap items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                  children: [
                    jsx('code', { className: 'text-xs', children: item.task_id }),
                    jsx('span', {
                      title: item.delivered_at_iso || '',
                      children: t('deliveredAtLabel', whenLabel(item.delivered_at, t))
                    }),
                    jsx('span', {
                      children: t(
                        'identityFull',
                        (item.identity && item.identity.task_id) || item.task_id,
                        String((item.identity && item.identity.revision) ?? '—'),
                        String((item.identity && item.identity.verdict_run_id) ?? '—'),
                        item.identity && item.identity.artifact_sha256
                          ? shortSha(item.identity.artifact_sha256)
                          : '—'
                      )
                    })
                  ]
                }),
                jsx(ReportSummary, {
                  report: item.report,
                  reportState: item.report_state,
                  reportCode: item.report_code,
                  t
                })
              ]
            },
            item.task_id
          )
        )
      })
    ]
  })
}

function TaskRow({ task, t }) {
  const title = task.title || task.task_id
  const review = task.review || {}
  const blocked = task.blocked
  const finalReport = task.final_report
  const readiness = task.delivery_readiness || null
  const reportState = finalReport ? finalReport.action || finalReport.state : null
  const identityText = identity =>
    identity
      ? t(
          'identityFull',
          identity.task_id || task.task_id,
          String(identity.revision ?? '—'),
          String(identity.verdict_run_id ?? '—'),
          identity.artifact_sha256 ? shortSha(identity.artifact_sha256) : '—'
        )
      : t('none')

  return jsxs('div', {
    className: 'flex flex-col gap-1 border-b border-(--ui-stroke-secondary) px-1 py-2 last:border-b-0',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-2',
        children: [
          jsx('span', { className: 'text-xs font-medium', children: title }),
          jsx('code', {
            className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
            children: task.task_id
          }),
          jsx(Tag, { children: task.role }),
          jsx(StatusBadge, { status: task.status, t }),
          jsx(Tag, { children: t('assigneeLine', task.assignee || t('unassigned')) })
        ]
      }),
      jsxs('div', {
        className: 'flex flex-wrap gap-x-4 gap-y-1 text-[0.6875rem] text-(--ui-text-tertiary)',
        children: [
          jsx('span', { children: t('lastActivity', ageLabel(task.updated_at, t)) }),
          jsx('span', {
            title: task.updated_at_iso || '',
            children: t('lastActivityAt', whenLabel(task.updated_at, t))
          }),
          jsx('span', { children: t('lastEvent', (task.last_event && task.last_event.kind) || t('never')) })
        ]
      }),
      blocked && (blocked.is_current || blocked.recorded)
        ? jsx('div', {
            className: 'text-xs',
            children: blocked.reason
              ? t('blockedReason', blocked.reason)
              : t('blockedNoReason')
          })
        : null,
      jsxs('div', {
        className: 'flex flex-wrap gap-x-4 gap-y-1 text-[0.6875rem] text-(--ui-text-tertiary)',
        children: [
          jsx('span', {
            children:
              review.verdict === 'PASS' && !review.held
                ? t('reviewPass', String(review.run_id || ''), review.reviewer || '')
                : review.verdict === 'REQUEST_CHANGES'
                  ? t('reviewChanges', String(review.run_id || ''), review.reviewer || '')
                  : review.held
                    ? t('reviewHeld', review.held_code || 'held')
                    : t('reviewNone')
          }),
          review.held && review.held_code
            ? jsx('code', { children: t('reviewHeldCode', review.held_code) })
            : null,
          review.verdict_conflict ? jsx('span', { children: t('reviewConflict') }) : null,
          jsx('span', { children: t('revisionLine', String(review.revision || 0)) }),
          review.artifact_sha256
            ? jsx('code', {
                title: review.artifact_sha256,
                children: t('artifactLine', shortSha(review.artifact_sha256))
              })
            : jsx('span', { children: t('artifactMissing') }),
          task.acceptance
            ? jsx('span', {
                children:
                  task.acceptance.current === false
                    ? t('acceptedStale', String(task.acceptance.revision || ''))
                    : t('acceptedLine', String(task.acceptance.revision || ''))
              })
            : jsx('span', { children: t('notAccepted') }),
          jsx('span', {
            children: finalReport
              ? reportState === 'unknown'
                ? t('reportUnknown', finalReport.code || 'unknown')
                : reportState === 'dead_letter'
                  ? t('reportDeadLetter', finalReport.code || 'dead_letter')
                  : reportState === 'in_flight'
                    ? t('reportInFlight')
                    : t('reportState', reportState)
              : t('reportState', 'none')
          }),
          readiness
            ? jsx('span', {
                children:
                  readiness.state === 'ready'
                    ? t(
                        'readyToDeliver',
                        String(review.revision || ''),
                        String(review.run_id || ''),
                        review.artifact_sha256 ? shortSha(review.artifact_sha256) : '—'
                      )
                    : readiness.state === 'held'
                      ? t('deliveryHeld', readiness.code || 'held')
                      : readiness.state === 'unknown'
                        ? t('deliveryUnknown', readiness.code || 'unknown')
                        : readiness.state === 'dead_letter'
                          ? t('deliveryDeadLetter', readiness.code || 'dead_letter')
                          : t(
                              'already_delivered',
                              identityText(readiness.stored_identity || readiness.delivered_identity)
                            )
              })
            : null,
          finalReport && finalReport.identity_conflict
            ? jsx('span', {
                children: t('delivery_identity_conflict', identityText(finalReport.delivered_identity))
              })
            : null,
          finalReport && finalReport.identity_conflict && readiness && readiness.current_identity
            ? jsx('span', {
                children: t('currentIdentity', identityText(readiness.current_identity))
              })
            : null,
          finalReport && finalReport.delivered_identity
            ? jsx('span', {
                children: t('storedIdentity', identityText(finalReport.delivered_identity))
              })
            : null,
          task.binding && task.binding.state !== 'verified'
            ? jsx('span', {
                children: task.binding.rebind_required
                  ? t('rebindRequired')
                  : t('bindingLine', task.binding.state, task.binding.source || '')
              })
            : null
        ]
      }),
      readiness && readiness.state === 'ready'
        ? jsx('div', {
            className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
            children: t('readyArtifactNote')
          })
        : null,
      finalReport && (finalReport.report || finalReport.report_code)
        ? jsx(ReportSummary, {
            report: finalReport.report,
            reportState: finalReport.report_state,
            reportCode: finalReport.report_code,
            t
          })
        : null,
      review.error
        ? jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: t('reviewUnknown', review.error) })
        : null
    ]
  })
}

// ── department detail ───────────────────────────────────────────────────────

function DetailView({ deptId, t, ctx }) {
  const query = useQuery({
    queryKey: ['department-hub', 'department', deptId],
    queryFn: () => ctx.rest(`/departments/${encodeURIComponent(deptId)}`),
    refetchInterval: REFRESH_MS,
    staleTime: 5000,
    retry: 1
  })

  if (query.isLoading) {
    return jsxs('div', {
      className: 'flex flex-col gap-2 p-3',
      children: [
        jsx(Skeleton, { className: 'h-6 w-48' }),
        jsx(Skeleton, { className: 'h-24 w-full' }),
        jsx(Skeleton, { className: 'h-24 w-full' })
      ]
    })
  }

  if (query.error) {
    return jsx(ErrorState, {
      title: t('detailErrorTitle'),
      description: t('detailErrorBody', String(query.error.message || query.error)),
      children: jsx(Button, {
        variant: 'secondary',
        onClick: () => query.refetch(),
        children: t('retry')
      })
    })
  }

  const payload = query.data || {}
  const dept = payload.department || {}
  const tasks = dept.tasks || []
  const work = dept.work || {}
  const events = payload.events || []

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-y-auto p-3',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-2',
        children: [
          jsx('h2', { className: 'text-base font-semibold', children: dept.name || deptId }),
          jsx(Tag, { children: dept.active ? t('active') : t('inactive') }),
          jsx(StatusBadge, { status: work.state || 'unknown', t })
        ]
      }),
      jsxs('div', {
        className: 'grid grid-cols-2 gap-2 md:grid-cols-4',
        children: [
          jsx(Field, { label: t('manager'), children: dept.manager || t('none') }),
          jsx(Field, { label: t('members'), children: (dept.members || []).join(', ') || t('none') }),
          jsx(Field, { label: t('reviewers'), children: (dept.reviewers || []).join(', ') || t('none') }),
          jsx(Field, { label: t('rootTask'), children: dept.root_task_id || t('none') })
        ]
      }),
      jsx(ManagerStatus, { status: dept.manager_status, t }),
      dept.membership && dept.membership.state !== 'verified'
        ? jsx('div', {
            role: 'status',
            className: 'rounded border border-(--ui-stroke-secondary) px-2 py-1 text-[0.6875rem] text-(--ui-text-secondary)',
            children: dept.membership.rebind_required
              ? t('rebindBanner')
              : t('membershipLine', dept.membership.state, String(dept.membership.explicit_bindings || 0))
          })
        : null,
      jsxs('div', {
        className: 'flex flex-wrap gap-x-4 gap-y-1 text-[0.6875rem] text-(--ui-text-tertiary)',
        children: [
          jsx('span', { children: t('maxRepair', String(dept.max_repair_rounds)) }),
          jsx('span', { children: t('taskCount', String(dept.task_count)) }),
          jsx('span', { children: t('lastUpdate', ageLabel(work.last_activity_at, t)) }),
          jsx('span', { children: t('stateSource', work.state_source || '') }),
          jsx('span', { children: t('membershipLine', (dept.membership && dept.membership.state) || 'unbound', String((dept.membership && dept.membership.explicit_bindings) || 0)) }),
          jsx('span', { children: t('reportState', (dept.final_report && dept.final_report.state) || 'none') })
        ]
      }),
      jsx(Separator, {}),
      jsx(DeliveredReports, {
        reports: (dept.final_report && dept.final_report.history) || [],
        t
      }),
      tasks.length === 0
        ? jsx(EmptyState, {
            title: t('noTasksTitle'),
            description: t('noTasksBody')
          })
        : jsxs('div', {
            className: 'flex flex-col',
            children: [
              jsx('h3', { className: 'mb-1 text-xs font-semibold', children: t('tasksHeading') }),
              tasks.map(task => jsx(TaskRow, { task, t }, task.task_id))
            ]
          }),
      dept.tasks_truncated
        ? jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: t('truncated') })
        : null,
      (dept.unknown_task_details || []).length
        ? jsxs('div', {
            className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
            children: [
              jsx('div', { className: 'font-medium', children: t('unknownTasksHeading') }),
              jsx('ul', {
                className: 'list-disc pl-4',
                children: dept.unknown_task_details.map(item =>
                  jsx('li', { children: t('unknownTaskLine', item.task_id, item.code) }, item.task_id)
                )
              })
            ]
          })
        : null,
      jsx(Separator, {}),
      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('h3', { className: 'text-xs font-semibold', children: t('eventsHeading') }),
          events.length === 0
            ? jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: t('eventsNone') })
            : jsx('ul', {
                className: 'flex flex-col gap-0.5 text-[0.6875rem] text-(--ui-text-tertiary)',
                children: events.map(event =>
                  jsx(
                    'li',
                    {
                      children: t(
                        'eventLine',
                        event.kind,
                        event.task_id,
                        event.state,
                        whenLabel(event.at, t)
                      )
                    },
                    event.event_id
                  )
                )
              })
        ]
      }),
      jsx('div', {
        className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
        children: t('snapshotLine', whenLabel(payload.generated_at, t))
      })
    ]
  })
}

// ── create department (explicitly blocked) ──────────────────────────────────

function CreatePanel({ t, ctx, onClose }) {
  const [form, setForm] = React.useState({
    id: '',
    name: '',
    manager_profile: '',
    members: '',
    reviewers: ''
  })
  const [plan, setPlan] = React.useState(null)
  const [refusal, setRefusal] = React.useState(null)
  const [busy, setBusy] = React.useState(false)

  const update = key => event => {
    const next = { ...form, [key]: event.target.value }
    setForm(next)
    setPlan(null)
    setRefusal(null)
  }

  const body = () => ({
    id: form.id.trim(),
    name: form.name.trim(),
    manager_profile: form.manager_profile.trim(),
    members: form.members
      .split(',')
      .map(item => item.trim())
      .filter(Boolean),
    reviewers: form.reviewers
      .split(',')
      .map(item => item.trim())
      .filter(Boolean)
  })

  const validate = () => {
    setBusy(true)
    setRefusal(null)
    ctx
      .rest('/departments/plan', { method: 'POST', body: body() })
      .then(value => setPlan(value))
      .catch(error => setRefusal({ code: 'plan_failed', reason: String(error.message || error) }))
      .finally(() => setBusy(false))
  }

  const submit = event => {
    if (event && typeof event.preventDefault === 'function') event.preventDefault()
    setBusy(true)
    setPlan(null)
    ctx
      .rest('/departments', { method: 'POST', body: body() })
      .then(value => setRefusal(value))
      .catch(error => {
        // A blocked mutation is an expected 409 carrying the refusal payload.
        const payload = error && error.body && typeof error.body === 'object' ? error.body : null
        setRefusal(payload || { code: 'refused', reason: String(error.message || error) })
      })
      .finally(() => setBusy(false))
  }

  return jsx('form', {
    'aria-label': t('createHeading'),
    className: 'flex flex-col gap-2 rounded border border-(--ui-stroke-secondary) p-3',
    onSubmit: submit,
    onKeyDown: event => {
      if (event.key === 'Escape') onClose()
    },
    children: jsxs('div', {
      className: 'flex flex-col gap-2',
      children: [
        jsxs('div', {
          className: 'flex items-center justify-between',
          children: [
            jsx('h3', { className: 'text-xs font-semibold', children: t('createHeading') }),
            jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: t('createReadOnly') })
          ]
        }),
        jsx(Input, { 'aria-label': t('fieldId'), placeholder: t('fieldId'), value: form.id, onChange: update('id') }),
        jsx(Input, { 'aria-label': t('fieldName'), placeholder: t('fieldName'), value: form.name, onChange: update('name') }),
        jsx(Input, {
          'aria-label': t('fieldManager'),
          placeholder: t('fieldManager'),
          value: form.manager_profile,
          onChange: update('manager_profile')
        }),
        jsx(Input, { 'aria-label': t('fieldMembers'), placeholder: t('fieldMembers'), value: form.members, onChange: update('members') }),
        jsx(Input, {
          'aria-label': t('fieldReviewers'),
          placeholder: t('fieldReviewers'),
          value: form.reviewers,
          onChange: update('reviewers')
        }),
        jsxs('div', {
          className: 'flex gap-2',
          children: [
            jsx(Button, { type: 'button', variant: 'secondary', disabled: busy, onClick: validate, children: t('validate') }),
            jsx(Button, { type: 'submit', disabled: busy, children: t('create') }),
            jsx(Button, { type: 'button', variant: 'ghost', onClick: onClose, children: t('close') })
          ]
        }),
        plan
          ? jsxs('div', {
              className: 'flex flex-col gap-1 text-[0.6875rem] text-(--ui-text-secondary)',
              children: [
                jsx('div', { children: t('planValid') }),
                jsx('div', { className: 'font-medium', children: t('planSteps') }),
                jsx('ol', {
                  className: 'list-decimal pl-4',
                  children: (plan.operator_steps || []).map((step, i) => jsx('li', { children: step }, String(i)))
                }),
                jsx('pre', {
                  className: 'overflow-x-auto rounded bg-(--chrome-action-hover) p-2 text-[0.6875rem]',
                  children: plan.config_snippet || ''
                })
              ]
            })
          : null,
        refusal
          ? jsxs('div', {
              role: 'status',
              className: 'flex flex-col gap-1 rounded border border-(--ui-stroke-secondary) p-2 text-[0.6875rem] text-(--ui-text-secondary)',
              children: [
                jsx('div', { className: 'font-medium', children: t('blockedTitle', refusal.code || '') }),
                jsx('div', { children: refusal.reason || '' }),
                refusal.what_you_can_do
                  ? jsx('ul', {
                      className: 'list-disc pl-4',
                      children: refusal.what_you_can_do.map((item, i) => jsx('li', { children: item }, String(i)))
                    })
                  : null
              ]
            })
          : null
      ]
    })
  })
}

// ── page ────────────────────────────────────────────────────────────────────

function DepartmentHubPage({ ctx }) {
  const t = usePluginI18n(ID)
  const query = useQuery({
    queryKey: ['department-hub', 'departments'],
    queryFn: () => ctx.rest('/departments'),
    refetchInterval: REFRESH_MS,
    staleTime: 5000,
    retry: 1
  })
  const [selected, setSelected] = React.useState(null)
  const [creating, setCreating] = React.useState(false)

  const departments = query.data ? query.data.departments || [] : []
  const degraded = query.data ? query.data.degraded || [] : []
  const activeId = selected || (departments.length ? departments[0].id : null)

  const header = jsxs('div', {
    className: 'flex flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
    children: [
      jsx('h1', { className: 'text-sm font-semibold', children: t('pageTitle') }),
      jsx(Tag, { children: t('readOnly') }),
      jsx(Tag, { children: t('sourceKanban') }),
      jsx('span', {
        'aria-live': 'polite',
        className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
        children: query.data
          ? t('refreshedAt', whenLabel(query.data.generated_at, t))
          : query.isFetching
            ? t('loading')
            : ''
      }),
      jsxs('div', {
        className: 'ml-auto flex items-center gap-2',
        children: [
          jsx(Button, {
            variant: 'secondary',
            'aria-label': t('refresh'),
            onClick: () => query.refetch(),
            children: t('refresh')
          }),
          jsx(Button, {
            'aria-label': t('newDepartment'),
            onClick: () => setCreating(value => !value),
            children: t('newDepartment')
          })
        ]
      })
    ]
  })

  const body = () => {
    if (query.isLoading) {
      return jsxs('div', {
        className: 'flex flex-col gap-2 p-3',
        children: [
          jsx(Skeleton, { className: 'h-6 w-40' }),
          jsx(Skeleton, { className: 'h-40 w-full' })
        ]
      })
    }
    if (query.error) {
      return jsx(ErrorState, {
        title: t('listErrorTitle'),
        description: t('listErrorBody', String(query.error.message || query.error)),
        children: jsx(Button, { variant: 'secondary', onClick: () => query.refetch(), children: t('retry') })
      })
    }
    if (departments.length === 0) {
      return jsx(EmptyState, {
        title: t('emptyTitle'),
        description: t('emptyBody')
      })
    }
    return jsxs('div', {
      className: 'flex h-full min-h-0',
      children: [
        jsx('aside', {
          className: 'w-64 shrink-0 overflow-y-auto border-r border-(--ui-stroke-secondary) p-2',
          children: jsx(DepartmentList, {
            departments,
            selectedId: activeId,
            onSelect: setSelected,
            t
          })
        }),
        jsx('section', {
          className: 'min-w-0 flex-1 overflow-hidden',
          children: activeId
            ? jsx(DetailView, { deptId: activeId, t, ctx })
            : jsx(EmptyState, { title: t('pickTitle'), description: t('pickBody') })
        })
      ]
    })
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    children: [
      header,
      creating
        ? jsx('div', {
            className: 'px-3 py-2',
            children: jsx(CreatePanel, {
              t,
              ctx,
              onClose: () => setCreating(false)
            })
          })
        : null,
      degraded.length
        ? jsx('div', {
            className: 'px-3 py-1 text-[0.6875rem] text-(--ui-text-tertiary)',
            children: t('degradedLine', degraded.map(item => item.code).join(', '))
          })
        : null,
      jsx('div', {
        className: 'min-h-0 flex-1',
        children: body()
      }),
      jsx('div', {
        className: 'border-t border-(--ui-stroke-secondary) px-3 py-1 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: t('footerNote')
      })
    ]
  })
}

// ── i18n + registration ─────────────────────────────────────────────────────

const EN = {
  pageTitle: 'Departments',
  nav: 'Departments',
  openCommand: 'Open departments',
  readOnly: 'Read-only',
  sourceKanban: 'Source: Kanban',
  loading: 'Loading…',
  refresh: 'Refresh',
  retry: 'Retry',
  newDepartment: 'New department',
  refreshedAt: when => `snapshot ${when}`,
  snapshotLine: when => `Server snapshot: ${when}. Status is an observed snapshot; it is not a live push.`,
  footerNote:
    'Read-only view. A Hermes profile is a routing/identity binding, not a security sandbox. No credentials or transcripts are shown.',
  managerLine: profile => `Manager: ${profile}`,
  blockedCount: count => `${count} blocked`,
  reviewCount: count => `${count} awaiting review`,
  heldCount: count => `${count} held`,
  reportDelivered: 'Final report delivered',
  reportNotDelivered: 'Final report not delivered',
  reportUnknownShort: 'Final report state unknown',
  reportUnknown: code => `final report: unknown (${code})`,
  reportDeadLetter: code => `final report: dead-letter (${code})`,
  reportInFlight: 'final report: in flight (unconfirmed; never resent)',
  updatedAgo: ago => `updated ${ago}`,
  departmentListLabel: 'Departments (Arrow keys to move, Enter to open)',
  lastActivity: ago => `last activity ${ago}`,
  lastActivityAt: at => `at ${at}`,
  lastEvent: kind => `last event: ${kind}`,
  assigneeLine: who => `assignee: ${who}`,
  unassigned: 'unassigned',
  manager: 'Manager',
  members: 'Members',
  reviewers: 'Reviewers',
  rootTask: 'Root card',
  maxRepair: rounds => `max repair rounds: ${rounds}`,
  taskCount: count => `bound cards: ${count}`,
  lastUpdate: ago => `last update ${ago}`,
  stateSource: source => `state ${source}`,
  tasksHeading: 'Bound work cards',
  noTasksTitle: 'No bound cards',
  noTasksBody:
    'Bind a board-verified root card assigned to this manager (department_bind) to see work here.',
  truncated: 'Only the first cards are shown (bounded refresh).',
  unknownTasksHeading: 'Unreadable cards (reported as unknown, never guessed)',
  unknownTaskLine: (task, code) => `${task}: ${code}`,
  eventsHeading: 'Durable event log (store receipts, newest first)',
  eventsNone: 'No events recorded yet.',
  eventLine: (kind, task, state, at) => `${kind} · ${task} · ${state} · ${at}`,
  blockedReason: reason => `Blocked: ${reason}`,
  blockedNoReason: 'Blocked on the board without a recorded reason.',
  reviewPass: (run, who) => `review PASS · run ${run} · ${who}`,
  reviewChanges: (run, who) => `review REQUEST_CHANGES · run ${run} · ${who}`,
  reviewHeld: code => `review held (${code})`,
  reviewHeldCode: code => `held: ${code}`,
  reviewNone: 'no independent review verdict yet',
  reviewConflict: 'conflicting verdicts on this revision',
  revisionLine: revision => `revision ${revision}`,
  artifactLine: sha => `worker-declared artifact ${sha}`,
  artifactMissing: 'no producer-recorded artifact SHA (artifact_unbound)',
  acceptedLine: revision => `manager accepted revision ${revision}`,
  acceptedStale: revision => `manager acceptance is stale (revision ${revision}, not the current one)`,
  notAccepted: 'not accepted by the manager',
  reportState: state => `final report: ${state}`,
  identityFull: (task, revision, run, sha) =>
    `task ${task} · revision ${revision} · run ${run} · SHA-256 ${sha}`,
  currentIdentity: identity => `current identity: ${identity}`,
  storedIdentity: identity => `stored delivered identity: ${identity}`,
  already_delivered: identity =>
    `Already delivered — existing report; no duplicate created · ${identity}`,
  delivery_identity_conflict: identity =>
    `A different identity was already delivered; original report preserved, cannot be overwritten · stored ${identity}`,
  deliveredHeading: 'Delivered — plugin-persisted reports',
  deliveredSubheading:
    'Committed into the plugin SQLite database (deliveries.body_json), not a board comment. Readable even when the board is unavailable or a later review rejects.',
  deliveredAtLabel: when => `delivered ${when}`,
  deliveredSummaryLabel: 'Accepted report summary',
  reportGeneration: generation => `generation ${generation}`,
  reportBytes: bytes => `${bytes} bytes`,
  reportTooLarge:
    'report_too_large — the stored report exceeds the display bound (65,536 bytes / 4,096 summary characters) and is not rendered',
  reportLegacy: 'legacy report (not a v5 plugin report; content is not rendered)',
  readyToDeliver: (revision, run, sha) =>
    `Ready to deliver — manager-accepted evidence: revision ${revision} · review run ${run} · worker-declared SHA-256 ${sha}`,
  readyArtifactNote:
    'Ready to deliver means the manager accepted this exact revision / run / SHA. The artifact hash is supplied by the worker; it is not a reviewer byte attestation.',
  deliveryHeld: code => `delivery held (${code})`,
  deliveryUnknown: code => `delivery unknown (${code}) — not resent`,
  deliveryDeadLetter: code => `delivery dead-letter (${code})`,
  bindingLine: (state, source) => `binding ${state} (${source})`,
  rebindRequired: 'Membership changed — rebind required before trusting this card',
  rebindBanner:
    'Membership moved or no longer resolves. Re-run department_bind as the operator; this page never re-binds automatically.',
  membershipLine: (state, explicit) => `membership: ${state} · explicit bindings ${explicit}`,
  managerStatusLabel: 'Manager status (three separate checks, never one light)',
  subscriptionLabel: 'Subscription',
  ownerLabel: 'Session owner',
  wakeLabel: 'Wake confirmed',
  // Nested on purpose: the Desktop resolver walks dot paths into NESTED objects
  // (`runtime.ts` resolvePath). A flat `'subscription.missing'` key is invisible
  // to it and renders as the raw key. Keep these groups nested.
  subscription: {
    subscribed: (matching, foreign) => `subscribed · ${matching} matching · ${foreign} foreign`,
    missing: 'not subscribed',
    duplicate: (matching) => `duplicate subscriptions (${matching})`,
    foreign_only: (matching, foreign) => `foreign subscription only (${foreign})`,
    unknown: 'subscription state unknown'
  },
  owner: {
    unverified: code => `unverified (${code})`,
    verified: 'verified'
  },
  wake: {
    unconfirmed: code => `unconfirmed (${code})`,
    confirmed: 'confirmed'
  },
  reviewUnknown: detail => `review evidence unreadable: ${detail}`,
  createHeading: 'Create department',
  createReadOnly: 'validated only — nothing is written by this page',
  fieldId: 'id (a-z0-9-_)',
  fieldName: 'Display name',
  fieldManager: 'Manager profile',
  fieldMembers: 'Members (comma separated)',
  fieldReviewers: 'Reviewers (comma separated)',
  validate: 'Validate',
  create: 'Create',
  close: 'Close',
  planValid: 'Declaration is valid. Apply it as the Hermes operator using these steps:',
  planSteps: 'Operator steps',
  blockedTitle: code => `Blocked by the backend (${code})`,
  listErrorTitle: 'Cannot read departments',
  listErrorBody: detail => `The backend did not answer: ${detail}`,
  detailErrorTitle: 'Cannot read this department',
  detailErrorBody: detail => `The backend did not answer: ${detail}`,
  emptyTitle: 'No departments configured',
  emptyBody:
    'Add one under plugins.entries.department-hub.settings.departments, then restart the gateway. This page cannot write config.',
  pickTitle: 'Select a department',
  pickBody: 'Pick a department on the left to see its work, review evidence and final report state.',
  degradedLine: codes => `degraded: ${codes}`,
  active: 'active',
  inactive: 'inactive',
  none: '—',
  never: 'never',
  statusTitle: status => `Kanban status: ${status}`,
  status: {
    blocked: 'Blocked',
    done: 'Done',
    review: 'In review',
    running: 'Running',
    ready: 'Ready',
    todo: 'Queued',
    triage: 'Triage',
    scheduled: 'Scheduled',
    archived: 'Archived',
    queued: 'Queued',
    unknown: 'Unknown'
  }
}

const ZH_HANT = {
  pageTitle: '部門',
  nav: '部門',
  openCommand: '開啟部門管理',
  readOnly: '唯讀',
  sourceKanban: '來源：Kanban',
  loading: '載入中…',
  refresh: '重新整理',
  retry: '重試',
  newDepartment: '新增部門',
  refreshedAt: when => `快照 ${when}`,
  snapshotLine: when => `伺服器快照：${when}。狀態是觀測快照，不是即時推播。`,
  footerNote:
    '唯讀檢視。Hermes profile 是路由／身分綁定，不是安全沙箱。不顯示憑證或對話內容。',
  managerLine: profile => `主管：${profile}`,
  blockedCount: count => `${count} 個阻塞`,
  reviewCount: count => `${count} 個待審查`,
  heldCount: count => `${count} 個保留`,
  reportDelivered: '最終報告已交付',
  reportNotDelivered: '最終報告未交付',
  reportUnknownShort: '最終報告狀態未知',
  reportUnknown: code => `最終報告：未知（${code}）`,
  reportDeadLetter: code => `最終報告：死信（${code}）`,
  reportInFlight: '最終報告：送出中（未確認，不會重送）',
  updatedAgo: ago => `更新於 ${ago}`,
  departmentListLabel: '部門清單（方向鍵移動，Enter 開啟）',
  lastActivity: ago => `最後活動 ${ago}`,
  lastActivityAt: at => `時間 ${at}`,
  lastEvent: kind => `最後事件：${kind}`,
  assigneeLine: who => `負責：${who}`,
  unassigned: '未指派',
  manager: '主管',
  members: '成員',
  reviewers: '審查者',
  rootTask: '根卡片',
  maxRepair: rounds => `最大修正輪數：${rounds}`,
  taskCount: count => `綁定卡片：${count}`,
  lastUpdate: ago => `最後更新 ${ago}`,
  stateSource: source => `狀態 ${source}`,
  tasksHeading: '綁定的工作卡片',
  noTasksTitle: '沒有綁定卡片',
  noTasksBody: '請用 department_bind 綁定一張指派給此主管、且經看板驗證的根卡片。',
  truncated: '僅顯示前幾張卡片（有界更新）。',
  unknownTasksHeading: '無法讀取的卡片（如實標示 unknown，不猜測）',
  unknownTaskLine: (task, code) => `${task}：${code}`,
  eventsHeading: '永久事件紀錄（store 收據，新到舊）',
  eventsNone: '尚未記錄任何事件。',
  eventLine: (kind, task, state, at) => `${kind} · ${task} · ${state} · ${at}`,
  blockedReason: reason => `阻塞原因：${reason}`,
  blockedNoReason: '看板標示阻塞，但沒有記錄原因。',
  reviewPass: (run, who) => `審查 PASS · run ${run} · ${who}`,
  reviewChanges: (run, who) => `審查 REQUEST_CHANGES · run ${run} · ${who}`,
  reviewHeld: code => `審查保留（${code}）`,
  reviewHeldCode: code => `保留：${code}`,
  reviewNone: '尚無獨立審查結論',
  reviewConflict: '同一版本出現互相衝突的結論',
  revisionLine: revision => `版本 ${revision}`,
  artifactLine: sha => `artifact（worker 宣告）${sha}`,
  artifactMissing: '沒有 producer 記錄的 artifact SHA（artifact_unbound）',
  acceptedLine: revision => `主管已接受版本 ${revision}`,
  acceptedStale: revision => `主管接受的是舊版本 ${revision}，不是目前版本`,
  notAccepted: '主管尚未接受',
  reportState: state => `最終報告：${state}`,
  identityFull: (task, revision, run, sha) =>
    `任務 ${task} · 版本 ${revision} · run ${run} · SHA-256 ${sha}`,
  currentIdentity: identity => `目前 identity：${identity}`,
  storedIdentity: identity => `已存交付 identity：${identity}`,
  already_delivered: identity =>
    `已交付過，沿用既有報告，未重複建立 · ${identity}`,
  delivery_identity_conflict: identity =>
    `已有不同版本交付，原報告保留，不可覆寫 · 已存 ${identity}`,
  deliveredHeading: '已交付（plugin 持久報告）',
  deliveredSubheading:
    '已寫入 plugin SQLite（deliveries.body_json），不是看板留言；即使看板不可用或之後出現新的 rejection，歷史報告仍可讀。',
  deliveredAtLabel: when => `交付時間 ${when}`,
  deliveredSummaryLabel: '已接受的報告摘要',
  reportGeneration: generation => `第 ${generation} 世代`,
  reportBytes: bytes => `${bytes} bytes`,
  reportTooLarge:
    'report_too_large——報告超過顯示上限（65,536 bytes／摘要 4,096 字），不顯示內容',
  reportLegacy: '舊版報告（非 v5 plugin 報告，不顯示內容）',
  readyToDeliver: (revision, run, sha) =>
    `可交付——主管已接受此 exact evidence：版本 ${revision} · 審查 run ${run} · worker 宣告 SHA-256 ${sha}`,
  readyArtifactNote:
    '「可交付」只表示主管接受了這個 revision／run／SHA。artifact 雜湊由 worker 宣告，不是 reviewer 對檔案的位元驗證，也不是 reviewer 蓋章。',
  deliveryHeld: code => `交付保留（${code}）`,
  deliveryUnknown: code => `交付狀態不明（${code}）——不會重送`,
  deliveryDeadLetter: code => `交付死信（${code}）`,
  bindingLine: (state, source) => `綁定 ${state}（${source}）`,
  rebindRequired: '成員拓撲已變動——需要重新綁定後才可信任此卡片',
  rebindBanner:
    '成員綁定已移動或無法解析。請由操作者重跑 department_bind；此頁不會自動重新綁定。',
  membershipLine: (state, explicit) => `成員綁定：${state} · 明示綁定 ${explicit} 筆`,
  managerStatusLabel: '主管狀態（三項獨立檢查，不是單一綠燈）',
  subscriptionLabel: '訂閱',
  ownerLabel: 'Session owner',
  wakeLabel: '已確認喚醒',
  // 巢狀是必要的：Desktop 解析器只走巢狀物件，扁平點號鍵會被當成原始鍵名顯示。
  subscription: {
    subscribed: (matching, foreign) => `已訂閱 · ${matching} 筆符合 · ${foreign} 筆外來`,
    missing: '未訂閱',
    duplicate: matching => `重複訂閱（${matching}）`,
    foreign_only: (matching, foreign) => `僅有外來訂閱（${foreign}）`,
    unknown: '訂閱狀態未知'
  },
  owner: {
    unverified: code => `未驗證（${code}）`,
    verified: '已驗證'
  },
  wake: {
    unconfirmed: code => `未確認（${code}）`,
    confirmed: '已確認'
  },
  reviewUnknown: detail => `審查證據無法讀取：${detail}`,
  createHeading: '新增部門',
  createReadOnly: '僅驗證——此頁不會寫入任何內容',
  fieldId: 'id（a-z0-9-_)',
  fieldName: '顯示名稱',
  fieldManager: '主管 profile',
  fieldMembers: '成員（逗號分隔）',
  fieldReviewers: '審查者（逗號分隔）',
  validate: '驗證',
  create: '建立',
  close: '關閉',
  planValid: '宣告格式有效。請以身分為 Hermes 操作者套用以下步驟：',
  planSteps: '操作步驟',
  blockedTitle: code => `後端已阻擋（${code}）`,
  listErrorTitle: '無法讀取部門清單',
  listErrorBody: detail => `後端沒有回應：${detail}`,
  detailErrorTitle: '無法讀取此部門',
  detailErrorBody: detail => `後端沒有回應：${detail}`,
  emptyTitle: '尚未設定部門',
  emptyBody:
    '請在 plugins.entries.department-hub.settings.departments 新增，然後重啟 gateway。此頁不能寫入設定。',
  pickTitle: '選擇部門',
  pickBody: '從左側選擇部門，查看工作狀態、審查證據與最終報告狀態。',
  degradedLine: codes => `降級：${codes}`,
  active: '啟用',
  inactive: '停用',
  none: '—',
  never: '從未',
  statusTitle: status => `Kanban 狀態：${status}`,
  status: {
    blocked: '阻塞',
    done: '完成',
    review: '審查中',
    running: '執行中',
    ready: '待執行',
    todo: '排隊中',
    triage: '待規格化',
    scheduled: '已排程',
    archived: '已封存',
    queued: '排隊中',
    unknown: '未知'
  }
}

export default {
  id: ID,
  name: 'Department Hub',
  defaultEnabled: false,
  register(ctx) {
    ctx.i18n.register({ en: EN, 'zh-hant': ZH_HANT })

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: PAGE_PATH },
        render: () => jsx(DepartmentHubPage, { ctx })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 60,
        data: { codicon: 'organization', label: ctx.i18n.t('nav'), path: PAGE_PATH }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'department-hub.open',
          label: ctx.i18n.t('openCommand'),
          keywords: ['department', 'manager', 'kanban', 'organization'],
          run: () => host.navigate(PAGE_PATH)
        }
      }
    ])
  }
}
