import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const installerSource = fs.readFileSync(
  path.join(process.cwd(), 'build', 'installer.nsh'),
  'utf8',
)

test('installer preserves install data before electron-builder removes the old directory', () => {
  const removeHook = installerSource.match(
    /!macro customRemoveFiles([\s\S]*?)!macroend/,
  )?.[1]
  assert.ok(removeHook)
  assert.match(removeHook, /PCIDS-InstallDataBackup/)
  assert.match(removeHook, /robocopy\.exe[\s\S]*\/MIR/)
  assert.match(removeHook, /app_data\.db/)
  assert.ok(
    removeHook.indexOf('robocopy.exe') < removeHook.indexOf('RMDir /r "$INSTDIR"'),
  )
})

test('installer restores preserved data before applying package defaults', () => {
  const installHook = installerSource.match(/!macro customInstall([\s\S]*?)!macroend/)?.[1]
  assert.ok(installHook)
  assert.match(installHook, /PCIDS-InstallDataBackup/)
  assert.match(installHook, /robocopy\.exe[\s\S]*\/E/)
  assert.ok(
    installHook.indexOf('Restoring PCIDS data') <
      installHook.indexOf('File /oname=agent-discovery.yaml'),
  )
})
