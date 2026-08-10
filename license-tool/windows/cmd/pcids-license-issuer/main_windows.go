//go:build windows

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/lxn/walk"
	. "github.com/lxn/walk/declarative"
	"pcids-license-tool/internal/issuer"
)

func executableDir() string {
	executable, err := os.Executable()
	if err != nil {
		return "."
	}
	return filepath.Dir(executable)
}

func main() {
	var window *walk.MainWindow
	var issuerDirEdit, dataRootEdit, customerIDEdit, customerNameEdit *walk.LineEdit
	var limitEdit, expiresEdit, machineCodeEdit *walk.LineEdit
	var resultEdit *walk.TextEdit

	issuerDir := executableDir()
	dataRoot := filepath.Join(issuerDir, "data")

	browseFolder := func(target *walk.LineEdit, title string) {
		dialog := new(walk.FileDialog)
		dialog.Title = title
		dialog.FilePath = strings.TrimSpace(target.Text())
		accepted, err := dialog.ShowBrowseFolder(window)
		if err != nil {
			walk.MsgBox(window, "选择目录失败", err.Error(), walk.MsgBoxIconError)
			return
		}
		if accepted {
			target.SetText(dialog.FilePath)
		}
	}

	refreshMachineCode := func() {
		identity, err := issuer.MachineIdentity(strings.TrimSpace(dataRootEdit.Text()))
		if err != nil {
			walk.MsgBox(window, "读取机器码失败", err.Error(), walk.MsgBoxIconError)
			return
		}
		machineCodeEdit.SetText(identity.MachineCode)
	}

	generateLicense := func() {
		limit, err := strconv.Atoi(strings.TrimSpace(limitEdit.Text()))
		if err != nil || limit < 1 {
			walk.MsgBox(window, "参数错误", "授权机器总数必须是大于 0 的整数", walk.MsgBoxIconWarning)
			return
		}
		result, err := issuer.IssueLicense(issuer.IssueOptions{
			IssuerDir:         strings.TrimSpace(issuerDirEdit.Text()),
			DataRoot:          strings.TrimSpace(dataRootEdit.Text()),
			CustomerID:        strings.TrimSpace(customerIDEdit.Text()),
			CustomerName:      strings.TrimSpace(customerNameEdit.Text()),
			InstallationLimit: limit,
			ExpiresOn:         strings.TrimSpace(expiresEdit.Text()),
		})
		if err != nil {
			walk.MsgBox(window, "签发失败", err.Error(), walk.MsgBoxIconError)
			return
		}
		machineCodeEdit.SetText(result.MachineCode)
		resultEdit.SetText(fmt.Sprintf(
			"License 已生成并写入：\r\n%s\r\n\r\n授权编号：%s\r\n机器序号：%d / %d",
			result.LicensePath, result.LicenseID, result.InstallationNo, result.InstallationLimit,
		))
		walk.MsgBox(window, "签发成功", "License 已写入 PCIDS 的 data\\license 目录。", walk.MsgBoxIconInformation)
	}

	if _, err := (MainWindow{
		AssignTo: &window,
		Title:    "PCIDS 离线授权签发工具",
		MinSize:  Size{Width: 760, Height: 650},
		Size:     Size{Width: 800, Height: 690},
		Layout:   VBox{MarginsZero: false, Spacing: 12},
		Children: []Widget{
			Label{Text: "PCIDS 离线机器授权", Font: Font{Family: "Microsoft YaHei UI", PointSize: 18, Bold: true}},
			Label{Text: "签发文件仅绑定当前计算机。完成后请从目标机移除签发工具、私钥、密码和台账。", TextColor: walk.RGB(102, 112, 133)},
			HSeparator{},
			Composite{Layout: HBox{}, Children: []Widget{
				Label{Text: "签发资料目录", MinSize: Size{Width: 112}},
				LineEdit{AssignTo: &issuerDirEdit, Text: issuerDir},
				PushButton{Text: "选择...", OnClicked: func() { browseFolder(issuerDirEdit, "选择签发资料目录") }},
			}},
			Composite{Layout: HBox{}, Children: []Widget{
				Label{Text: "软件 data 目录", MinSize: Size{Width: 112}},
				LineEdit{AssignTo: &dataRootEdit, Text: dataRoot},
				PushButton{Text: "选择...", OnClicked: func() { browseFolder(dataRootEdit, "选择 PCIDS 的 data 目录") }},
			}},
			Composite{Layout: HBox{}, Children: []Widget{
				Label{Text: "客户编号", MinSize: Size{Width: 112}},
				LineEdit{AssignTo: &customerIDEdit},
			}},
			Composite{Layout: HBox{}, Children: []Widget{
				Label{Text: "客户名称", MinSize: Size{Width: 112}},
				LineEdit{AssignTo: &customerNameEdit},
			}},
			Composite{Layout: HBox{}, Children: []Widget{
				Label{Text: "授权机器总数", MinSize: Size{Width: 112}},
				LineEdit{AssignTo: &limitEdit, Text: "1", MaxSize: Size{Width: 160}},
				HSpacer{},
			}},
			Composite{Layout: HBox{}, Children: []Widget{
				Label{Text: "授权截止日期", MinSize: Size{Width: 112}},
				LineEdit{AssignTo: &expiresEdit, CueBanner: "YYYY-MM-DD；留空表示长期有效"},
			}},
			Composite{Layout: HBox{}, Children: []Widget{
				Label{Text: "本机机器码", MinSize: Size{Width: 112}},
				LineEdit{AssignTo: &machineCodeEdit, ReadOnly: true},
				PushButton{Text: "读取", OnClicked: refreshMachineCode},
			}},
			TextEdit{AssignTo: &resultEdit, ReadOnly: true, MinSize: Size{Height: 130}, VScroll: true},
			Composite{Layout: HBox{}, Children: []Widget{
				HSpacer{},
				PushButton{Text: "生成并安装 License", MinSize: Size{Width: 180, Height: 38}, OnClicked: generateLicense},
			}},
		},
	}).Run(); err != nil {
		walk.MsgBox(nil, "启动失败", err.Error(), walk.MsgBoxIconError)
	}
}
