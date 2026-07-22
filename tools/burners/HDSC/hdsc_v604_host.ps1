param(
  [Parameter(Mandatory = $true)][ValidateSet('preflight', 'validate', 'flash')][string]$Command,
  [string]$Reader = '',
  [string]$TargetChip = '',
  [string]$FirmwarePath = '',
  [string]$EraseMode = 'chip',
  [string]$CompletionAction = 'reset-run',
  [int]$BaudIndex = 0,
  [int]$BaudKHz = 0,
  [int]$BaudRate = 0,
  [string]$VendorExe = ''
)

$ErrorActionPreference = 'Stop'

function Write-Result([bool]$Ok, [object]$Data, [string]$ErrorText = '') {
  $json = [pscustomobject]@{
    ok = $Ok
    data = $Data
    error = $ErrorText
  } | ConvertTo-Json -Compress -Depth 6

  # Windows PowerShell 5.1 writes pipeline text using the active OEM code
  # page when stdout is redirected.  Vendor exceptions contain Chinese text,
  # which then becomes '?' before the Python agent can parse the JSON.
  # Write UTF-8 bytes directly so task logs retain the original diagnostics.
  $utf8 = [System.Text.UTF8Encoding]::new($false)
  $bytes = $utf8.GetBytes("$json`r`n")
  $stream = [Console]::OpenStandardOutput()
  $stream.Write($bytes, 0, $bytes.Length)
  $stream.Flush()
}

function Normalise-Target([string]$Value) {
  return ([regex]::Replace(([string]$Value).ToUpperInvariant(), '[^A-Z0-9]', ''))
}

try {
  if ([string]::IsNullOrWhiteSpace($VendorExe)) {
    throw 'VendorExe is required. Set HDSC_CCID_V604_EXE.'
  }
  if (-not (Test-Path -LiteralPath $VendorExe)) {
    throw "HDSC CCID Prog V6.04 was not found: $VendorExe. Set HDSC_CCID_V604_EXE."
  }
  if ([string]::IsNullOrWhiteSpace($Reader)) {
    throw 'A PC/SC reader name is required.'
  }

  $assembly = [Reflection.Assembly]::LoadFrom((Resolve-Path -LiteralPath $VendorExe))
  $writerType = $assembly.GetType('HdscCcidIsp.HdWriter', $true)
  $writerFlags = [Reflection.BindingFlags]'InvokeMethod,Static,Public'
  $writerFields = [Reflection.BindingFlags]'Static,Public,NonPublic'
  $connected = $writerType.InvokeMember('Connect', $writerFlags, $null, $null, @($Reader))
  if (-not $connected) {
    $writerError = $writerType.GetField('gErrMsg', $writerFields).GetValue($null)
    throw ("Unable to connect HDSC CCID Writer: {0}" -f $writerError)
  }

  try {
    $firmware = $writerType.InvokeMember('GetFirewareVer', $writerFlags, $null, $null, @())
    if ($Command -eq 'preflight') {
      Write-Result $true ([pscustomobject]@{ reader = $Reader; firmware = $firmware; vendor_exe = $VendorExe })
      exit 0
    }

    if ([string]::IsNullOrWhiteSpace($TargetChip) -or [string]::IsNullOrWhiteSpace($FirmwarePath)) {
      throw 'flash requires target-chip and firmware-path.'
    }
    if (-not (Test-Path -LiteralPath $FirmwarePath -PathType Leaf)) {
      throw "Firmware file does not exist: $FirmwarePath"
    }

    $table = [Activator]::CreateInstance($assembly.GetType('HdscCcidIsp.McuTable', $true))
    $requested = Normalise-Target $TargetChip
    # Windows PowerShell 5 does not expose this vendor method through its
    # dynamic adapter although reflection reports it correctly.
    $analysisMethods = @($table.GetType().GetMethods() | Where-Object { $_.Name -eq 'Analysis' })
    if ($analysisMethods.Count -ne 1) {
      throw 'HDSC V6.04 McuTable.Analysis method was not found.'
    }
    $availableMcus = $analysisMethods[0].Invoke($table, $null)
    $matches = @($availableMcus | Where-Object {
      $actual = Normalise-Target $_.BusinessName
      $actual -eq $requested -or $actual.StartsWith($requested) -or $requested.StartsWith($actual)
    })
    if ($matches.Count -ne 1) {
      $names = @($matches | ForEach-Object BusinessName) -join ', '
      throw "V6.04 could not uniquely match target '$TargetChip'; candidates: $names"
    }
    $mcu = $matches[0]

    $supportedBauds = @(([string]$mcu.UartBauds -split ',' | ForEach-Object { $_.Trim() }) | Where-Object { $_ })
    if ($supportedBauds.Count -eq 0) {
      throw "V6.04 target '$($mcu.BusinessName)' exposes no ISP baud rates."
    }
    $effectiveBaudIndex = $BaudIndex
    if ($BaudRate -gt 0) {
      $requestedBaud = [string]$BaudRate
      $effectiveBaudIndex = [Array]::IndexOf([string[]]$supportedBauds, $requestedBaud)
      if ($effectiveBaudIndex -lt 0) {
        throw "V6.04 target '$($mcu.BusinessName)' does not support $BaudRate baud; supported rates: $($supportedBauds -join ', ') baud."
      }
    }
    elseif ($BaudKHz -gt 0) {
      $requestedBaud = [string]($BaudKHz * 1000)
      $effectiveBaudIndex = [Array]::IndexOf([string[]]$supportedBauds, $requestedBaud)
      if ($effectiveBaudIndex -lt 0) {
        throw "V6.04 target '$($mcu.BusinessName)' does not support ${BaudKHz} kHz; supported rates: $($supportedBauds -join ', ') baud."
      }
    }
    if ($effectiveBaudIndex -lt 0 -or $effectiveBaudIndex -ge $supportedBauds.Count) {
      throw "V6.04 baud index $effectiveBaudIndex is outside the supported range 0..$($supportedBauds.Count - 1) for '$($mcu.BusinessName)'."
    }
    # Despite its Int32 signature, V6.04 ISP_ConnectAndPps expects the actual
    # baud value (for example 115200), not the combo-box index. Passing 0
    # produces F100000000 and makes an otherwise valid HC32L130 connection
    # fail; the official GUI passes 115200 and produces F100C20100.
    $effectiveBaud = [int]$supportedBauds[$effectiveBaudIndex]

    function Invoke-McuBool([object]$Mcu, [string]$MethodName, [object[]]$Arguments, [string]$Stage) {
      $methods = @($Mcu.GetType().GetMethods() | Where-Object {
        $_.Name -eq $MethodName -and $_.GetParameters().Count -eq $Arguments.Count
      })
      if ($methods.Count -ne 1) { throw "V6.04 method '$MethodName' was not found for $Stage." }
      $invokeArguments = New-Object object[] $Arguments.Count
      for ($index = 0; $index -lt $Arguments.Count; $index++) {
        $value = $Arguments[$index]
        $invokeArguments[$index] = if ($null -eq $value) { $null } else { $value.PSObject.BaseObject }
      }
      try {
        $ok = $methods[0].Invoke($Mcu.PSObject.BaseObject, $invokeArguments)
      }
      catch {
        $inner = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
        throw "$Stage failed: $inner"
      }
      if (-not [bool]$ok) { throw "$Stage failed: V6.04 returned false." }
      return $true
    }

    $hexType = $assembly.GetType('HdscCcidIsp.HdHexFile', $true)
    # ``xxxFile_Convert2List`` belongs to the encrypted online/remote GUI
    # path.  PCIDS artifacts are ordinary Intel HEX, for which V6.04 exposes
    # this plaintext/default converter.
    $convertMethods = @($hexType.GetMethods() | Where-Object {
      $_.Name -eq 'DftFile_Convert2List' -and $_.GetParameters().Count -eq 2
    })
    if ($convertMethods.Count -ne 1) {
      throw 'HDSC V6.04 plaintext HEX conversion method was not found.'
    }
    $convert = $convertMethods[0]
    # The plaintext converter fills this ByRef metadata dictionary.
    $hexSymbols = [System.Collections.Generic.Dictionary[string,string]]::new()
    # Build a real CLR object array by indexed assignment.  A PowerShell
    # array literal wraps generic values in PSObject, which cannot be passed
    # to the vendor method's ref Dictionary parameters.
    $convertArgs = New-Object object[] 2
    $convertArgs[0] = (Resolve-Path -LiteralPath $FirmwarePath).Path
    $convertArgs[1] = $hexSymbols
    try {
      [System.Collections.Generic.List[string]]$flashZones = $convert.Invoke($null, $convertArgs)
    }
    catch {
      $inner = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
      throw "V6.04 HEX conversion failed: $inner"
    }
    if ($null -eq $flashZones -or $flashZones.Count -eq 0) {
      throw 'Firmware contains no programmable Flash data.'
    }

    # This validates the complete input conversion path without sending a
    # target-connect, erase, program, or run command to the CCID writer.
    if ($Command -eq 'validate') {
      Write-Result $true ([pscustomobject]@{
        reader = $Reader
        firmware = $firmware
        target = $mcu.BusinessName
        hdsc_family = $mcu.HdscName
        flash_zones = $flashZones.Count
        baud_index = $effectiveBaudIndex
        baud_rate = $effectiveBaud
        supported_baud_rates = $mcu.UartBauds
      })
      exit 0
    }

    try {
      [System.Collections.Generic.List[string]]$programScripts = $mcu.ISP_Scripts_ProgramAndVerify($flashZones)
    }
    catch {
      $inner = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
      throw "V6.04 program-script generation failed: $inner"
    }
    if ($null -eq $programScripts -or $programScripts.Count -eq 0) {
      throw 'V6.04 produced no program/verify scripts for the supplied firmware.'
    }

    $steps = [ordered]@{}
    $ispActive = $false
    $operationSucceeded = $false
    try {
      # Match the V6.04 GUI configuration used for HC32L130: the writer
      # supplies the target with 3.3 V before the BOOT/RST ISP handshake.
      # Always create a real power edge: a previous failed operation may
      # have left VCC enabled, in which case another PowerOn command alone
      # cannot make the target sample BOOT and enter ISP again.
      $null = $writerType.InvokeMember('PowerOff', $writerFlags, $null, $null, @())
      Start-Sleep -Milliseconds 500
      $null = $writerType.InvokeMember('PowerOn_3V3', $writerFlags, $null, $null, @())
      $steps.power = 'off -> 3.3V on'

      # HC32L130/L006 does not enter the UART ISP directly.  The official
      # V6.04 online-programming flow first connects through PreISP, uploads
      # the MCU-specific RAMCode to SRAM and runs it; only then does it issue
      # the normal UART ISP connect/PPS sequence.  Skipping this stage makes
      # the first 49 06 handshake time out even though the same wiring works
      # in the vendor GUI.
      $ramCodeName = [string]$mcu.RamCodeName
      if (-not [string]::IsNullOrWhiteSpace($ramCodeName)) {
        $preConnectMethods = @($mcu.GetType().GetMethods() | Where-Object {
          $_.Name -eq 'PreISP_ConnectAndPps' -and $_.GetParameters().Count -eq 1
        })
        if ($preConnectMethods.Count -ne 1) {
          throw 'V6.04 PreISP_ConnectAndPps method was not found.'
        }
        $preConnectArgs = New-Object object[] 1
        $preConnectArgs[0] = ''
        try {
          $preConnected = $preConnectMethods[0].Invoke($mcu.PSObject.BaseObject, $preConnectArgs)
        }
        catch {
          $inner = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
          throw "PreISP target connect failed: $inner"
        }
        if (-not [bool]$preConnected) {
          $preConnectError = [string]$preConnectArgs[0]
          if ([string]::IsNullOrWhiteSpace($preConnectError)) { $preConnectError = 'V6.04 returned false.' }
          throw "PreISP target connect failed: $preConnectError"
        }
        $steps.preisp_connect = 'executed'

        try {
          $ramCodeScripts = @($mcu.PreISP_Scripts_DownLoadRamcodeAndRun($ramCodeName))
        }
        catch {
          $inner = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
          throw "V6.04 RAMCode script generation failed: $inner"
        }
        if ($null -eq $ramCodeScripts -or $ramCodeScripts.Count -eq 0) {
          throw "V6.04 produced no RAMCode scripts for '$ramCodeName'."
        }
        # Match the GUI loop exactly. Windows PowerShell eagerly unwraps the
        # vendor List<string> into Object[], so passing the whole collection
        # back into PreISP_ScriptList_Exe is not type-safe.
        for ($ramIndex = 0; $ramIndex -lt $ramCodeScripts.Count; $ramIndex++) {
          $null = Invoke-McuBool $mcu 'PreISP_Script_Exe' @([string]$ramCodeScripts[$ramIndex]) "Download and run RAMCode script $($ramIndex + 1)/$($ramCodeScripts.Count)"
        }
        $steps.ramcode = [pscustomobject]@{
          name = $ramCodeName
          scripts = $ramCodeScripts.Count
          executed = $true
        }
      }

      $null = Invoke-McuBool $mcu 'ISP_ConnectAndPps' @($effectiveBaud) 'Connect target'
      $steps.connect = 'executed'
      $steps.baud_rate = $effectiveBaud
      $ispActive = $true
      if ($EraseMode -notin @('none', 'no-erase')) {
        $null = Invoke-McuBool $mcu 'ISP_Flash_ChipErase' @() 'Chip erase'
        $steps.erase = 'executed'
      }
      # The official online-programming form also executes generated program
      # scripts one by one. This avoids the same List<string> -> Object[]
      # conversion at the actual Flash programming stage.
      for ($programIndex = 0; $programIndex -lt $programScripts.Count; $programIndex++) {
        $null = Invoke-McuBool $mcu 'ISP_Script_Exe' @([string]$programScripts[$programIndex]) "Program and verify script $($programIndex + 1)/$($programScripts.Count)"
      }
      $steps.program_verify = [pscustomobject]@{ executed = $true; scripts = $programScripts.Count }
      if ($CompletionAction -in @('reset-run', 'run')) {
        $steps.run = [pscustomobject]@{
          executed = $false
          reason = 'V6.04 L006 exposes no target reset/jump command; manually press RST after programming.'
        }
      }
      $null = Invoke-McuBool $mcu 'ISP_ExitIsp' @() 'Exit ISP'
      $steps.exit = 'executed'
      $ispActive = $false
      if ($CompletionAction -in @('reset-run', 'run')) {
        $null = $writerType.InvokeMember('PowerOff', $writerFlags, $null, $null, @())
        Start-Sleep -Milliseconds 500
        $null = $writerType.InvokeMember('PowerOn_3V3', $writerFlags, $null, $null, @())
        $steps.run = [pscustomobject]@{
          executed = $true
          method = 'writer 3.3V power cycle'
        }
      }
      else {
        $null = $writerType.InvokeMember('PowerOff', $writerFlags, $null, $null, @())
        $steps.power_off = 'executed'
      }
      $operationSucceeded = $true
    }
    finally {
      if ($ispActive) {
        try {
          $null = Invoke-McuBool $mcu 'ISP_ExitIsp' @() 'Exit ISP after failed operation'
        }
        catch {
          # Preserve the original programming error; disconnection below is
          # still required even if this best-effort target cleanup fails.
        }
      }
      if (-not $operationSucceeded) {
        try {
          $null = $writerType.InvokeMember('PowerOff', $writerFlags, $null, $null, @())
        }
        catch {
          # Preserve the original target/programming error.
        }
      }
    }

    Write-Result $true ([pscustomobject]@{
      reader = $Reader
      firmware = $firmware
      target = $mcu.BusinessName
      hdsc_family = $mcu.HdscName
      steps = $steps
    })
  }
  finally {
    $null = $writerType.InvokeMember('DisConnect', $writerFlags, $null, $null, @())
  }
}
catch {
  Write-Result $false $null $_.Exception.Message
  exit 2
}
