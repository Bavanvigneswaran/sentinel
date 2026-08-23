// Metro, with one addition: the local native module in `modules/`.
//
// `modules/sentinel-collector` is autolinked on the *native* side by
// expo-modules-autolinking, which is what makes the Kotlin compile and register.
// Its TypeScript surface is a different question — Metro resolves bare
// specifiers out of node_modules, and this package is deliberately not there.
// Mapping it here (with the matching `paths` entry in tsconfig.json) lets the
// app import it by name, so a screen reads `from "sentinel-collector"` rather
// than reaching up out of src/ with a relative path.
const { getDefaultConfig } = require("expo/metro-config")
const path = require("node:path")

const config = getDefaultConfig(__dirname)

const collector = path.resolve(__dirname, "modules/sentinel-collector")
config.resolver.extraNodeModules = {
  ...config.resolver.extraNodeModules,
  "sentinel-collector": collector,
}
// Metro only watches the project root's own tree by default once a module is
// resolved from outside node_modules; naming it keeps fast refresh working when
// the module's TypeScript changes.
config.watchFolders = [...(config.watchFolders ?? []), collector]

module.exports = config
