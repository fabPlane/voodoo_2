package voodoo

import voodoo.artix.{AmdAc701Gen, CoreArtixGen}
import voodoo.diff.GenOne
import voodoo.gowin.{CoreGowinGen, TangConsole138kGen, TangMega138kProGen}

/** The one `main` for this project, declared as `//> using mainClass` in
  * project.scala.
  *
  * scala-cli derives its Bloop project hash from `--main-class`, so every
  * distinct `scala-cli run . --main-class X` used to get its own full compile
  * of the 22k-line tree (and `compile` / `test` a third and fourth). Routing
  * every generator through this object keeps compile, test and all `run`s in
  * a single incremental cache — see AGENTS.md, "Scala: keep it warm".
  *
  *   scala-cli run . -- console            # rtl/gowin/TangConsole138k.v
  *   scala-cli run . -- gowin              # rtl/gowin/TangMega138kPro.v
  *   scala-cli run . -- artix              # rtl/artix/AmdAc701.v
  *   scala-cli run . -- core-gowin | core-artix
  *   scala-cli run . -- one <Module> [outDir] [--async-reset]   # voodoo.diff.GenOne
  */
object Gen {
  private val targets: Seq[(String, Array[String] => Unit)] = Seq(
    "console" -> (_ => TangConsole138kGen.main(Array.empty)),
    "gowin" -> (_ => TangMega138kProGen.main(Array.empty)),
    "artix" -> (_ => AmdAc701Gen.main(Array.empty)),
    "core-gowin" -> (_ => CoreGowinGen.main(Array.empty)),
    "core-artix" -> (_ => CoreArtixGen.main(Array.empty)),
    "one" -> (rest => GenOne.main(rest))
  )

  def main(args: Array[String]): Unit = args.toList match {
    case name :: rest if targets.exists(_._1 == name) =>
      targets.collectFirst { case (`name`, run) => run }.get(rest.toArray)
    case _ =>
      System.err.println(
        s"usage: scala-cli run . -- <${targets.map(_._1).mkString("|")}> [args]"
      )
      sys.exit(2)
  }
}
