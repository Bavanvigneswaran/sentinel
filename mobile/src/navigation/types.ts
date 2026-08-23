/**
 * The route table, as types. React Navigation's equivalent of
 * web/src/routes.tsx.
 *
 * A tab navigator nested in a stack: the four tabs are the things you switch
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
  /** Reached from the More tab rather than a tab each. */
  Anomalies: undefined
  Forecasts: undefined
  Incidents: undefined
  Reports: undefined
  Settings: undefined
}

export type TabParamList = {
  Fleet: undefined
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
