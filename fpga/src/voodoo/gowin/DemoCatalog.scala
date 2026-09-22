package voodoo.gowin

/** Console menu: Glide2x SDK tests on the root list, publisher DOS demos
  * under OTHER DEMOS. Playable items are packed SST-1 streams walked by
  * DemoAxiLitePlayer. Official `grSplash` / `traces/logo` is ~100k PCI
  * commands; the chevron stand-in is compact. Game demos have no on-chip
  * capture — archives live in `sw/glide-demos/`.
  */
object DemoCatalog {
  case class Entry(
      title: String,
      packedFile: Option[String],
      playable: Boolean,
      usePlayer: Boolean,
      folder: Boolean,
      loopAt: Int,
      hint: String,
      info: Seq[String]
  )

  val logoLoopAt = 362

  private def sdk(
      title: String,
      file: String,
      loopAt: Int,
      hint: String,
      info: Seq[String]
  ): Entry =
    Entry(title, Some(file), playable = true, usePlayer = true, folder = false, loopAt, hint, info)

  private def publisherDemo(
      title: String,
      hint: String,
      archive: String,
      extra: Seq[String] = Seq.empty
  ): Entry = {
    val info =
      Seq(
        " " + title.replace("  ", " "),
        "",
        " DOS GLIDE PUBLISHER DEMO.",
        " ARCHIVE IN SW/GLIDE-DEMOS",
        " " + archive,
        ""
      ) ++ extra ++ Seq(
        " NO ON-CHIP SST TRACE.",
        " TOO BIG FOR BRAM.",
        "",
        " B = BACK"
      )
    Entry(title, None, playable = false, usePlayer = false, folder = false, 0, hint, info)
  }

  val main: Seq[Entry] = Seq(
    sdk(
      "3DFX  LOGO  SPIN",
      "demo_logo_spin.bin",
      logoLoopAt,
      "SST-1 CHEVRON  GRSPLASH-STYLE",
      Seq(
        " 3DFX LOGO SPIN",
        "",
        " COMPACT SST-1 CHEVRON.",
        " TEST04 FBI INIT + LOOP.",
        " FULL GRSPLASH IS 100K CMDS.",
        "",
        " A = RUN   B = BACK"
      )
    ),
    sdk(
      "GLIDE  TEST04",
      "demo_test04.bin",
      0,
      "GOURAUD TRIANGLE  GLIDE SDK",
      Seq(
        " GLIDE TEST04",
        "",
        " GOURAUD TRIANGLE.",
        " GLIDE2X SST1 SDK.",
        " PACKED TRACE ON BAR.",
        "",
        " A = RUN   B = BACK"
      )
    ),
    sdk(
      "GLIDE  TEST16",
      "demo_test16.bin",
      0,
      "3DFX SPLASH  SHAMELESS PLUG",
      Seq(
        " GLIDE TEST16",
        "",
        " SHAMELESS PLUG + SPLASH.",
        " GLIDE2X SST1 SDK.",
        " PACKED TRACE ON BAR.",
        "",
        " A = RUN   B = BACK"
      )
    ),
    sdk(
      "GLIDE  ALPHABET",
      "demo_test_alphabet.bin",
      0,
      "TEXTURED TEXT  GLIDE SDK",
      Seq(
        " GLIDE ALPHABET",
        "",
        " TEXTURED GLYPHS.",
        " GLIDE2X SST1 SDK.",
        " PACKED TRACE ON BAR.",
        "",
        " A = RUN   B = BACK"
      )
    ),
    Entry(
      "SD CARD  PLAY",
      None,
      playable = true,
      usePlayer = false,
      folder = false,
      0,
      "V2GL FROM SD OR JTAG",
      Seq(
        " SD CARD PLAY",
        "",
        " PACKED GLIDE ON THE CARD.",
        " COPY GLIDE/PLAY.V2G THEN",
        " JTAG LOAD TO DDR 0x11000000",
        " (SW/GLIDE-SD).",
        "",
        " A = RUN   B = BACK"
      )
    ),
    Entry(
      "QUAKE  RISC-V  SW",
      None,
      playable = false,
      usePlayer = false,
      folder = false,
      0,
      "SOFTWARE QUAKE ON DDR",
      Seq(
        " QUAKE RISC-V SOFTWARE",
        "",
        " WINQUAKE 320X200 BACKEND.",
        " SOFT CPU + 256MIB DDR.",
        " PAK AT 0X90000000.",
        " SEE SW/QUAKE-RISCV.",
        "",
        " B = BACK"
      )
    ),
    Entry(
      "OTHER  DEMOS",
      None,
      playable = false,
      usePlayer = false,
      folder = true,
      0,
      "DOS GLIDE  PUBLISHER DEMOS",
      Seq(
        " OTHER DEMOS",
        "",
        " PUBLISHER DOS GLIDE DEMOS.",
        " A OPENS THE SUBMENU.",
        "",
        " B = BACK"
      )
    )
  )

  val other: Seq[Entry] = Seq(
    publisherDemo(
      "TOMB  RAIDER",
      "DOS GLIDE  HOST TRACE REPLAY",
      "TOMBDEMO / TOMB3DEM",
      Seq(
        " TRACE IS 52K PCI CMDS.",
        " HOST: MAKE NATIVE/SIM/",
        " CHECK/TOMB",
        ""
      )
    ),
    publisherDemo("SCREAMER  2", "DOS GLIDE  RACING DEMO", "SCREAMER-2.ZIP"),
    publisherDemo("EXTREME  ASSAULT", "DOS 3DFX  HELICOPTER DEMO", "EXTREME-ASSAULT.EXE"),
    publisherDemo("CARMAGEDDON  SPLAT", "DOS 3DFX  SPLAT PACK DEMO", "CARMAGEDDON-SPLAT-PACK.ZIP"),
    publisherDemo("ARCHIMEDEAN  DYNASTY", "DOS GLIDE  SUBMARINE DEMO", "ARCHIMEDEAN-DYNASTY.EXE"),
    publisherDemo("TOSHINDEN", "DOS 3DFX  FIGHTER DEMO", "TOSHINDEN-3DFX.ZIP"),
    publisherDemo("NASCAR  RACING  2", "DOS GLIDE  RACING DEMO", "NASCAR-RACING-2.ZIP"),
    publisherDemo("GRAND  THEFT  AUTO", "DOS GLIDE  CITY DEMO", "GTA.ZIP"),
    publisherDemo("LORDS  OF  THE  REALM  2", "DOS GLIDE  STRATEGY DEMO", "LORDS-OF-THE-REALM-2.ZIP"),
    publisherDemo("MONSTER  3D  DISC", "DIAMOND  BUNDLE DEMO DISC", "MONSTER-3D-DEMO-DISC.7Z")
  )

  val logoIndex: Int = 0
  val test04Index: Int = 1
  val test16Index: Int = 2
  val alphabetIndex: Int = 3
  val sdCardIndex: Int = 4
  val quakeSwIndex: Int = 5
  val otherDemosIndex: Int = 6
  val tombIndex: Int = 0

  val mainCount: Int = main.length
  val otherCount: Int = other.length
  val count: Int = mainCount
  val screenCount: Int = 2 * (mainCount + otherCount)

  val otherMenuBase: Int = mainCount
  val mainInfoBase: Int = mainCount + otherCount
  val otherInfoBase: Int = 2 * mainCount + otherCount

  val playerEntries: Seq[Entry] = main.filter(_.usePlayer)
  val playerFiles: Seq[String] = playerEntries.flatMap(_.packedFile)
  val playerLoopAt: Seq[Int] = playerEntries.map(_.loopAt)
  val playerBankByCursor: Seq[Int] = main.indices.map { i =>
    val b = playerEntries.indexWhere(_ == main(i))
    if (b < 0) 0 else b
  }
  val mainPlayable: Seq[Boolean] = main.map(_.playable)
  val otherPlayable: Seq[Boolean] = other.map(_.playable)
}

object DemoOsd {
  val cols = 40
  val rows = 15

  def pad(s: String): String = s.padTo(cols, ' ').take(cols)

  def fit(lines: Seq[String]): Seq[String] =
    lines.take(rows).padTo(rows, "").map(pad)

  private def listScreen(head: Seq[String], items: Seq[DemoCatalog.Entry], cursor: Int): Seq[String] = {
    val marked = items.zipWithIndex.map { case (e, i) =>
      val mark = if (i == cursor) '>' else ' '
      s" $mark ${e.title}"
    }
    val hint = Seq("", " " + items(cursor).hint)
    fit(head ++ marked ++ hint)
  }

  def mainMenuScreen(cursor: Int): Seq[String] =
    listScreen(
      Seq(
        " SPINALVOODOO / TANG CONSOLE 138K",
        "",
        " USB PAD: D-PAD MOVE  A RUN  B BACK",
        " KEYS: USER=NEXT  RESET=RUN",
        ""
      ),
      DemoCatalog.main,
      cursor
    )

  def otherMenuScreen(cursor: Int): Seq[String] =
    listScreen(
      Seq(
        " OTHER  DEMOS",
        " USB PAD: D-PAD  A OPEN  B BACK",
        ""
      ),
      DemoCatalog.other,
      cursor
    )

  val screens: Seq[Seq[String]] = {
    val built =
      DemoCatalog.main.indices.map(mainMenuScreen) ++
        DemoCatalog.other.indices.map(otherMenuScreen) ++
        DemoCatalog.main.map(e => fit(e.info)) ++
        DemoCatalog.other.map(e => fit(e.info))
    require(built.length == DemoCatalog.screenCount)
    built.foreach { s =>
      require(s.length == rows)
      s.foreach(line => require(line.length == cols, line))
    }
    built
  }
}
