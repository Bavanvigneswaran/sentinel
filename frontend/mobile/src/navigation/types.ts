/**
 * The route table, as types. React Navigation's equivalent of
 * web/src/routes.tsx.
 *
 * A tab navigator nested in a stack: the three tabs are the things you switch
 * between, and everything else is pushed on top of whichever tab you came
 * from. Anomalies/Forecasts/Incidents/Reports live on the stack behind the
 * More tab rather than as tabs of their own — a bottom bar stops being usable
 * past about five entries.
 * `deviceName` is passed as a param purely so the header has something to show
 * before the device fetch lands — the screen still fetches the real record.
 */

import type { BottomTabScreenProps } from "@react-navigation/bottom-tabs"
import type { CompositeScreenProps } from "@react-navigation/native"
import type { NativeStackScreenProps } from "@react-navigation/native-stack"

export type RootStackParamList = {
  Tabs: undefined
  Device: { deviceId: string; deviceName?: string }
  Live: { deviceId: string; deviceName?: string }
  /** Phase 10b: this phone as a monitored device, not as a viewer of others. */
  Collector: undefined
  /** Reached two ways, which is what the optional param is for: from the More
   * tab with no params (fleet-wide, as before), or from a device's own screen
   * with `deviceId` set (that machine only). Every backing endpoint already
   * took `device_id` as a query param, so scoping these cost no backend
   * change — see app/api/routes/{alerts,forecasts,incidents,reports}.py.
   *
   * `| undefined` rather than a required object: React Navigation treats a
   * param type with no undefined in it as mandatory, and `navigate("Reports")`
   * from the More menu would stop typechecking. */
  /** One device's history over a chosen window — always device-scoped, so
   * unlike the four below it takes a required param. There is no fleet-wide
   * history: a chart of every machine's CPU at once is not a thing anybody
   * reads. */
  History: { deviceId: string; deviceName?: string }
  /** One incident's timeline and AI insights. Reached from the incidents
   * list, which only holds `Incident` — the timeline needs a second request
   * per incident and is not worth making for every row. */
  IncidentDetail: { incidentId: string }
  Anomalies: { deviceId?: string; deviceName?: string } | undefined
  Forecasts: { deviceId?: string; deviceName?: string } | undefined
  Incidents: { deviceId?: string; deviceName?: string } | undefined
  Reports: { deviceId?: string; deviceName?: string } | undefined
  /** Same optional-scope shape as the four above, but filtered client-side:
   * `/alerts/rules` has no device_id parameter, because a rule with a null
   * device_id genuinely applies to every device and a server-side filter
   * would have to decide whether those belong in one device's list. */
  AlertRules: { deviceId?: string; deviceName?: string } | undefined
  Settings: undefined
}

export type TabParamList = {
  /** One tab, not the two this had. Fleet and Devices both listed every
   * machine and both opened Device; the fleet card was already a superset of
   * the plain list's — same name, hostname, OS and status badge, plus the
   * health score, the headline numbers and an hour of sparkline. The only
   * things the plain list had to itself were "last seen" and a Live shortcut,
   * which now live on the fleet card. A bottom bar has four slots worth using
   * and spending two of them on the same list was the worst way to use them.
   * The web console keeps `/` and `/devices` separate: a sidebar has room a
   * bottom bar does not. */
  Devices: undefined
  Alerts: undefined
  /** A menu, not a screen of its own — see MoreScreen for why these four are
   *  not four more tabs. */
  More: undefined
}

export type RootStackScreenProps<T extends keyof RootStackParamList> = NativeStackScreenProps<
  RootStackParamList,
  T
>

/** A tab screen still needs to `navigate("Device", …)`, which lives on the
 * parent stack — hence the composite. */
export type RootTabScreenProps<T extends keyof TabParamList> = CompositeScreenProps<
  BottomTabScreenProps<TabParamList, T>,
  NativeStackScreenProps<RootStackParamList>
>
